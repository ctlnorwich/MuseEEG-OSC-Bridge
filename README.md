## Muse OSC Bridge

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
