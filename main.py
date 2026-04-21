"""Run the Muse LSL streamer and OSC bridge as a single command."""

from __future__ import annotations

import multiprocessing as mp
from argparse import ArgumentParser

from osc_bridge import start_bridge_processes, stop_bridge_processes
from stream_supervisor import MuseStreamSupervisor


def build_parser() -> ArgumentParser:
    """Create the command-line interface for the standalone bridge app."""
    parser = ArgumentParser()
    parser.add_argument(
        "--aux",
        action="store_true",
        default=False,
        help="Include Muse AUX channel in forwarded EEG samples",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose output for reconnects and OSC messages",
    )
    parser.add_argument(
        "--osc-ip",
        type=str,
        default="127.0.0.1",
        help="OSC server IP address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--osc-port",
        type=int,
        default=9000,
        help="OSC server port (default: 9000)",
    )
    parser.add_argument(
        "--muse-address",
        type=str,
        default=None,
        help="Target a specific Muse by Bluetooth address",
    )
    parser.add_argument(
        "--muse-name",
        type=str,
        default=None,
        help="Target a specific Muse by advertised name",
    )
    parser.add_argument(
        "--backend",
        type=str,
        default="auto",
        help="muselsl backend to use (default: auto)",
    )
    parser.add_argument(
        "--interface",
        type=str,
        default=None,
        help="Bluetooth interface passed through to muselsl",
    )
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=3.0,
        help="Seconds to wait before restarting muselsl after disconnect",
    )
    parser.add_argument(
        "--no-ppg",
        action="store_true",
        default=False,
        help="Disable the PPG stream",
    )
    parser.add_argument(
        "--no-acc",
        action="store_true",
        default=False,
        help="Disable the accelerometer stream",
    )
    parser.add_argument(
        "--no-gyro",
        action="store_true",
        default=False,
        help="Disable the gyroscope stream",
    )
    return parser


def main() -> int:
    """Start the Muse streamer supervisor and reconnecting OSC bridge."""
    mp.freeze_support()
    args, _ = build_parser().parse_known_args()

    supervisor = MuseStreamSupervisor(
        address=args.muse_address,
        name=args.muse_name,
        backend=args.backend,
        interface=args.interface,
        ppg_enabled=not args.no_ppg,
        acc_enabled=not args.no_acc,
        gyro_enabled=not args.no_gyro,
        reconnect_delay_seconds=args.reconnect_delay,
        verbose=args.verbose,
    )
    supervisor.start()

    processes = start_bridge_processes(
        use_aux=args.aux,
        osc_ip=args.osc_ip,
        osc_port=args.osc_port,
        ppg_enabled=not args.no_ppg,
        acc_enabled=not args.no_acc,
        gyro_enabled=not args.no_gyro,
        verbose=args.verbose,
    )

    try:
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        print("Stopping Muse bridge...")
    finally:
        supervisor.stop()
        stop_bridge_processes(processes)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
