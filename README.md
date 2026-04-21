## Muse OSC Bridge

Connects to a [Muse EEG headband](https://choosemuse.com) over BLE (Bluetooth Low Energy) and
forwards all sensor data to OSC in real time, making it straightforward to
consume Muse data in tools like Max/MSP, TouchDesigner, Pure Data, or any other
OSC-capable environment.

Internally it uses [muselsl](https://github.com/alexandrebarachant/muse-lsl) to
stream sensor data over LSL (Lab Streaming Layer), then bridges each LSL stream
to OSC. If the Muse headband disconnects, the supervisor automatically restarts
muselsl and each bridge worker reconnects as soon as its stream reappears — no
manual intervention needed.

**Compatibility:** Tested with the **Muse 2** on **macOS**. The Muse S and
original Muse should also work via muselsl but are untested.

**Streams forwarded:**

| OSC address | Content |
|---|---|
| `/muse/eeg` | Raw EEG samples — channels TP9, AF7, AF8, TP10 (plus Right AUX with `--aux`) |
| `/muse/eeg_fft` | Rolling FFT magnitude spectrum of the EEG signal |
| `/muse/features/alpha_absolute` / `_relative` | Alpha band (8–12 Hz) power |
| `/muse/features/beta_absolute` / `_relative` | Beta band (12–30 Hz) power |
| `/muse/features/theta_absolute` / `_relative` | Theta band (4–8 Hz) power |
| `/muse/features/delta_absolute` / `_relative` | Delta band (1–4 Hz) power |
| `/muse/features/gamma_absolute` / `_relative` | Gamma band (30–45 Hz) power |
| `/muse/features/heartrate` | Smoothed heart rate in BPM (derived from PPG via AMPD peak detection) |
| `/muse/ppg` | Raw PPG samples |
| `/muse/acc` | Accelerometer (X, Y, Z) |
| `/muse/gyro` | Gyroscope (X, Y, Z) |

---

## Usage

Run the Muse streamer and OSC bridge with one command:

```bash
uv run muse-osc-bridge
```

That starts `muselsl` internally with EEG plus PPG, accelerometer, and gyroscope streams enabled, then forwards the LSL streams to OSC.

Useful options:

```bash
uv run muse-osc-bridge --verbose
uv run muse-osc-bridge --osc-ip 127.0.0.1 --osc-port 9000
uv run muse-osc-bridge --muse-name Muse-XXXX
uv run muse-osc-bridge --no-ppg --no-acc --no-gyro
```

If the Muse stream disconnects, the app will keep retrying the `muselsl` connection and each LSL-to-OSC worker will reconnect when its stream reappears.

---

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
