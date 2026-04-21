"""Reconnectable LSL-to-OSC bridge workers for Muse streams."""

from __future__ import annotations

from collections import deque
import multiprocessing as mp
from threading import Lock, Thread
import time

import ampdlib
import numpy as np
import pylsl
from pythonosc import udp_client
from scipy.signal import butter, filtfilt


LSL_SCAN_TIMEOUT = 3
LSL_MAX_SAMPLES = 1
LSL_PULL_TIMEOUT = 0.02  # Block up to 20ms per pull; prevents CPU busy-spin
STREAM_RECONNECT_DELAY_SECONDS = 1.0
STREAM_SILENCE_TIMEOUT_SECONDS = 5.0  # Allow time for muselsl to restart

FFT_COMPUTE_RATE = 10
FREQUENCY_BANDS_RATE = 10
PPG_HEARTRATE_RATE = 5

N_FFT = 256
SEND_MEAN_FEATURES = True
DEBUG_LOG_INTERVAL_SECONDS = 1.0

PPG_BUFFER_SIZE = 256
PPG_SAMPLE_RATE = 64.0
PPG_HR_FREQ_MIN = 0.8
PPG_HR_FREQ_MAX = 2.5
PPG_SMOOTHING_WINDOW = 20
PPG_CONFIDENCE_THRESHOLD = 0.3


class StreamDisconnectedError(RuntimeError):
    """Raised when an LSL stream stops producing samples for too long."""


def _summarise_osc_payload(payload):
    """Return a human-readable summary of an OSC payload."""
    if isinstance(payload, list):
        if payload and isinstance(payload[0], list):
            inner_size = len(payload[0])
            return f"nested_list outer={len(payload)} inner={inner_size}"
        if payload:
            values = ", ".join(f"{v:.6f}" for v in payload)
            return f"[{values}]"
        return "[]"
    return f"scalar {payload}"


def _resolve_stream(stream_type: str, verbose: bool = False) -> pylsl.StreamInfo:
    """Wait until an LSL stream of the requested type is available."""
    while True:
        streams = pylsl.resolve_byprop("type", stream_type, timeout=LSL_SCAN_TIMEOUT)
        if streams:
            stream = streams[0]
            if verbose:
                print(
                    f"[LSL][{stream_type}] connected to name={stream.name()!r} "
                    f"channels={stream.channel_count()} rate={stream.nominal_srate()}Hz"
                )
            return stream
        if verbose:
            print(f"[LSL][{stream_type}] waiting for stream...")
        time.sleep(STREAM_RECONNECT_DELAY_SECONDS)


def _read_next_sample(inlet: pylsl.StreamInlet, last_received_time: float):
    """Pull a single sample or raise when the stream has gone silent."""
    samples, timestamps = inlet.pull_chunk(
        timeout=LSL_PULL_TIMEOUT,
        max_samples=LSL_MAX_SAMPLES,
    )
    if timestamps:
        return samples[0], time.time()
    if time.time() - last_received_time > STREAM_SILENCE_TIMEOUT_SECONDS:
        raise StreamDisconnectedError(
            f"no samples received for {STREAM_SILENCE_TIMEOUT_SECONDS:.1f}s"
        )
    return None, last_received_time


def _debug_send(osc_client, address, payload, verbose, last_debug_log_time, log_key):
    """Send one OSC message and optionally emit rate-limited verbose logs."""
    try:
        osc_client.send_message(address, payload)
    except Exception as err:
        print(
            f"[OSC ERROR] failed {address}: {err} "
            f"payload={_summarise_osc_payload(payload)}"
        )
        return

    if not verbose:
        return

    now = time.time()
    last_log_time = last_debug_log_time.get(log_key, 0.0)
    if now - last_log_time >= DEBUG_LOG_INTERVAL_SECONDS:
        print(f"[OSC] sent {address} {_summarise_osc_payload(payload)}")
        last_debug_log_time[log_key] = now


def single_lsl_stream_to_osc(stream_type, osc_ip, osc_port, verbose=False):
    """Forward non-EEG/PPG LSL samples to OSC under /muse/<type>."""
    osc_client = udp_client.SimpleUDPClient(osc_ip, osc_port)
    last_debug_log_time = {}

    while True:
        try:
            stream = _resolve_stream(stream_type, verbose=verbose)
            inlet = pylsl.StreamInlet(stream, max_chunklen=LSL_MAX_SAMPLES)
            last_received_time = time.time()

            while True:
                sample, last_received_time = _read_next_sample(inlet, last_received_time)
                if sample is None:
                    continue
                address = f"/muse/{stream.type().lower()}"
                _debug_send(
                    osc_client,
                    address,
                    sample,
                    verbose,
                    last_debug_log_time,
                    stream_type.lower(),
                )
        except StreamDisconnectedError as err:
            print(f"[LSL][{stream_type}] disconnected: {err}; reconnecting...")
            time.sleep(STREAM_RECONNECT_DELAY_SECONDS)
        except Exception as err:
            print(f"[LSL][{stream_type}] worker failed: {err}; reconnecting...")
            time.sleep(STREAM_RECONNECT_DELAY_SECONDS)


def ppg_stream_to_osc(osc_ip, osc_port, verbose=False):
    """Forward raw PPG and publish AMPD-based heartrate features to OSC."""
    osc_client = udp_client.SimpleUDPClient(osc_ip, osc_port)
    last_debug_log_time = {}

    while True:
        ppg_buffer = deque(maxlen=PPG_BUFFER_SIZE)
        heartrate_history = deque(maxlen=PPG_SMOOTHING_WINDOW)
        last_heartrate_time = time.time()

        def _get_filtered_signal():
            """Return normalized, bandpass-filtered PPG or None when unavailable."""
            if len(ppg_buffer) < PPG_BUFFER_SIZE:
                return None
            ppg_data = np.array(list(ppg_buffer))
            ppg_avg = np.mean(ppg_data, axis=1)
            ppg_mean = np.mean(ppg_avg)
            ppg_std = np.std(ppg_avg)
            if ppg_std < 1e-6:
                return None
            ppg_normalized = (ppg_avg - ppg_mean) / ppg_std
            try:
                nyquist = PPG_SAMPLE_RATE / 2
                low = np.clip(PPG_HR_FREQ_MIN / nyquist, 0.01, 0.99)
                high = np.clip(PPG_HR_FREQ_MAX / nyquist, 0.01, 0.99)
                if low >= high:
                    return None
                b, a = butter(4, [low, high], btype="band")
                return filtfilt(b, a, ppg_normalized)
            except Exception:
                return None

        def _compute_heartrate_ampd():
            """Estimate heartrate as (bpm, confidence) from filtered PPG."""
            ppg_filtered = _get_filtered_signal()
            if ppg_filtered is None:
                return None, 0.0

            try:
                peaks = ampdlib.ampd(ppg_filtered)
            except Exception:
                return None, 0.0

            if len(peaks) < 2:
                return None, 0.0

            intervals = np.diff(peaks) / PPG_SAMPLE_RATE
            valid = intervals[
                (intervals >= 1.0 / PPG_HR_FREQ_MAX)
                & (intervals <= 1.0 / PPG_HR_FREQ_MIN)
            ]
            if len(valid) == 0:
                return None, 0.0

            mean_interval = np.mean(valid)
            heartrate_bpm = 60.0 / mean_interval
            cv = np.std(valid) / mean_interval if mean_interval > 0 else 1.0
            confidence = max(0.0, 1.0 - cv)
            return heartrate_bpm, confidence

        try:
            stream = _resolve_stream("PPG", verbose=verbose)
            inlet = pylsl.StreamInlet(stream, max_chunklen=LSL_MAX_SAMPLES)
            last_received_time = time.time()

            while True:
                sample, last_received_time = _read_next_sample(inlet, last_received_time)
                if sample is None:
                    continue

                ppg_buffer.append(sample)
                _debug_send(
                    osc_client,
                    "/muse/ppg",
                    sample,
                    verbose,
                    last_debug_log_time,
                    "ppg_raw",
                )

                now = time.time()
                if now - last_heartrate_time < 1.0 / PPG_HEARTRATE_RATE:
                    continue

                hr, confidence = _compute_heartrate_ampd()
                if hr is not None and confidence >= PPG_CONFIDENCE_THRESHOLD and 40 <= hr <= 180:
                    heartrate_history.append(hr)
                    smoothed_hr = float(np.mean(list(heartrate_history)))
                    if verbose:
                        print(
                            f"[OSC][PPG] heartrate raw={hr:.1f}bpm "
                            f"smoothed={smoothed_hr:.1f}bpm conf={confidence:.3f}"
                        )
                    _debug_send(
                        osc_client,
                        "/muse/features/heartrate",
                        smoothed_hr,
                        verbose,
                        last_debug_log_time,
                        "ppg_heartrate",
                    )
                elif verbose:
                    hr_str = f"{hr:.1f}bpm" if hr is not None else "None"
                    print(f"[OSC][PPG] rejected: hr={hr_str} conf={confidence:.3f}")

                last_heartrate_time = now
        except StreamDisconnectedError as err:
            print(f"[LSL][PPG] disconnected: {err}; reconnecting...")
            time.sleep(STREAM_RECONNECT_DELAY_SECONDS)
        except Exception as err:
            print(f"[LSL][PPG] worker failed: {err}; reconnecting...")
            time.sleep(STREAM_RECONNECT_DELAY_SECONDS)


def eeg_stream_to_osc(use_aux, osc_ip, osc_port, verbose=False):
    """Forward raw EEG and publish FFT bandpower features over OSC."""
    # muselsl channel order (source: github.com/alexandrebarachant/muse-lsl):
    #   0: TP9        (left ear)
    #   1: AF7        (left forehead)
    #   2: AF8        (right forehead)
    #   3: TP10       (right ear)
    #   4: Right AUX  (only present when --aux is passed; stripped otherwise)
    osc_client = udp_client.SimpleUDPClient(osc_ip, osc_port)
    last_debug_log_time = {}
    shared_list_lock = Lock()

    sample_rate = 256.0
    n_channels = 5 if use_aux else 4
    fft_buffer = np.ones((N_FFT, n_channels)) * 1e-6
    fft = np.abs(np.fft.rfft(fft_buffer, axis=0))
    stream_running = False

    def _compute_fft():
        """Continuously compute FFT and publish spectra while stream is running."""
        nonlocal fft
        while True:
            target_time = time.time() + 1 / FFT_COMPUTE_RATE
            if stream_running:
                with shared_list_lock:
                    fft = np.abs(np.fft.rfft(fft_buffer, axis=0))
                    fft_payload = (
                        np.mean(fft, axis=-1).astype(float).tolist()
                        if SEND_MEAN_FEATURES
                        else fft.tolist()
                    )
                _debug_send(
                    osc_client,
                    "/muse/eeg_fft",
                    fft_payload,
                    verbose,
                    last_debug_log_time,
                    "eeg_fft",
                )
            time.sleep(max(0.0, target_time - time.time()))

    def _power_band_to_osc(power_band_name, freq_min, freq_max):
        """Send one frequency band's power metrics at a fixed cadence."""
        while True:
            target_time = time.time() + 1 / FREQUENCY_BANDS_RATE

            if not stream_running:
                time.sleep(max(0.0, target_time - time.time()))
                continue

            idx_min = int(freq_min * N_FFT / sample_rate)
            idx_max = int(freq_max * N_FFT / sample_rate)
            with shared_list_lock:
                power = fft[idx_min:idx_max].copy()
                fft_snapshot = fft.copy()

            absolute_power = np.sum(power, axis=0)
            total_power = np.sum(fft_snapshot, axis=0)
            total_power[total_power == 0] = 1e-6
            relative_power = absolute_power / total_power

            if SEND_MEAN_FEATURES:
                absolute_payload = float(np.mean(absolute_power))
                relative_payload = float(np.mean(relative_power))
            else:
                absolute_payload = absolute_power.astype(float).tolist()
                relative_payload = relative_power.astype(float).tolist()

            _debug_send(
                osc_client,
                f"/muse/features/{power_band_name}_absolute",
                absolute_payload,
                verbose,
                last_debug_log_time,
                f"{power_band_name}_absolute",
            )
            _debug_send(
                osc_client,
                f"/muse/features/{power_band_name}_relative",
                relative_payload,
                verbose,
                last_debug_log_time,
                f"{power_band_name}_relative",
            )
            time.sleep(max(0.0, target_time - time.time()))

    Thread(target=_compute_fft, daemon=True, name="eeg_fft").start()
    for band_name, freq_min, freq_max in (
        ("alpha", 8, 12),
        ("beta", 12, 30),
        ("theta", 4, 8),
        ("delta", 1, 4),
        ("gamma", 30, 45),
    ):
        Thread(
            target=_power_band_to_osc,
            args=(band_name, freq_min, freq_max),
            daemon=True,
            name=band_name,
        ).start()

    while True:
        try:
            stream = _resolve_stream("EEG", verbose=verbose)
            inlet = pylsl.StreamInlet(stream, max_chunklen=LSL_MAX_SAMPLES)
            resolved_channels = inlet.info().channel_count()
            sample_rate = inlet.info().nominal_srate() or sample_rate
            n_channels = resolved_channels if use_aux else max(1, resolved_channels - 1)
            fft_buffer = np.ones((N_FFT, n_channels)) * 1e-6
            fft = np.abs(np.fft.rfft(fft_buffer, axis=0))
            last_received_time = time.time()
            stream_running = True

            while True:
                sample, last_received_time = _read_next_sample(inlet, last_received_time)
                if sample is None:
                    continue

                incoming_data = sample if use_aux else sample[:-1]
                _debug_send(
                    osc_client,
                    "/muse/eeg",
                    incoming_data,
                    verbose,
                    last_debug_log_time,
                    "raw_signal",
                )

                with shared_list_lock:
                    fft_buffer = np.roll(fft_buffer, -1, axis=0)
                    fft_buffer[-1, :] = incoming_data
        except StreamDisconnectedError as err:
            stream_running = False
            print(f"[LSL][EEG] disconnected: {err}; reconnecting...")
            time.sleep(STREAM_RECONNECT_DELAY_SECONDS)
        except Exception as err:
            stream_running = False
            print(f"[LSL][EEG] worker failed: {err}; reconnecting...")
            time.sleep(STREAM_RECONNECT_DELAY_SECONDS)


def start_bridge_processes(
    use_aux,
    osc_ip,
    osc_port,
    ppg_enabled=True,
    acc_enabled=True,
    gyro_enabled=True,
    verbose=False,
):
    """Start reconnecting worker processes for each enabled Muse stream."""
    process_specs = [
        ("EEG", eeg_stream_to_osc, (use_aux, osc_ip, osc_port, verbose)),
    ]
    if ppg_enabled:
        process_specs.append(("PPG", ppg_stream_to_osc, (osc_ip, osc_port, verbose)))
    if acc_enabled:
        process_specs.append(
            ("ACC", single_lsl_stream_to_osc, ("ACC", osc_ip, osc_port, verbose))
        )
    if gyro_enabled:
        process_specs.append(
            ("GYRO", single_lsl_stream_to_osc, ("GYRO", osc_ip, osc_port, verbose))
        )

    processes = []
    for stream_name, target, args in process_specs:
        process = mp.Process(target=target, args=args, name=f"{stream_name.lower()}-bridge")
        process.start()
        processes.append(process)
    return processes


def stop_bridge_processes(processes):
    """Terminate bridge worker processes when the app is shutting down."""
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(timeout=5)