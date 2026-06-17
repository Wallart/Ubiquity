from bleak import BleakScanner, BleakClient

import argparse
import asyncio


async def main(args: argparse.Namespace):
    print('scanning for 5 seconds, please wait...')

    devices = await BleakScanner.discover(return_adv=True, cb=dict(use_bdaddr=args.macos_use_bdaddr))
    for d, a in devices.values():
        print()
        print(d)
        print('-' * len(str(d)))
        print(a)

    client = BleakClient('BD374FAD-950C-A83E-F4A0-12564A33039D')
    try:
        await client.connect()
        print('ok')
        # model_number = await client.read_gatt_char(MODEL_NBR_UUID)
        # print("Model Number: {0}".format("".join(map(chr, model_number))))
    except Exception as e:
        print(e)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--macos-use-bdaddr',
        action='store_true',
        help='when true use Bluetooth address instead of UUID on macOS',
    )

    args = parser.parse_args()

    asyncio.run(main(args))