"""
Ubiquity — TCP bidirectional file sync
Usage:
  Server (macOS):   python main.py --mode server --dir /path/to/folder
  Client (Windows): python main.py --mode client --dir C:\path\to\folder --peer <server-ip>
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sync_engine import SyncEngine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%H:%M:%S',
)


async def async_main():
    parser = argparse.ArgumentParser(description='Ubiquity — BLE bidirectional file sync')
    parser.add_argument(
        '--mode', choices=['server', 'client'], required=True,
        help='server = BLE peripheral (advertise), client = BLE central (connect)',
    )
    parser.add_argument('--dir', required=True, help='Local directory to watch and sync')
    parser.add_argument(
        '--peer', default='127.0.0.1',
        help='IP address or hostname of the server (default: 127.0.0.1)',
    )
    args = parser.parse_args()

    watch_dir = Path(args.dir)
    if not watch_dir.is_dir():
        print(f'Error: {watch_dir} is not a directory', file=sys.stderr)
        sys.exit(1)

    engine = SyncEngine(str(watch_dir), args.mode, args.peer)
    await engine.run()


if __name__ == '__main__':
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print('\nStopped.')
