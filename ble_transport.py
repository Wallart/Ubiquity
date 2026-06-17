"""
BLE transport layer.

Topology:
  Server = BLE peripheral (bless) — advertises as "UbiquitySync"
  Client = BLE central   (bleak) — scans and connects

Two GATT characteristics:
  S2C_UUID  (notify)  — server pushes data to client
  C2S_UUID  (write)   — client pushes data to server

This achieves bidirectional file sync over a single BLE connection.
"""
import asyncio
import logging
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner

log = logging.getLogger(__name__)

SERVICE_UUID = '12345678-1234-1234-1234-123456789abc'
S2C_UUID     = '12345678-1234-1234-1234-123456780001'  # server → client (notify)
C2S_UUID     = '12345678-1234-1234-1234-123456780002'  # client → server (write)

OnReceive = Callable[[bytes], None]


class BLEServer:
    """Peripheral role: advertises, accepts connections, sends notifications."""

    def __init__(self, device_name: str, on_receive: OnReceive):
        self._name = device_name
        self._on_receive = on_receive
        self._server = None

    async def start(self):
        # Deferred import so Windows clients don't need bless/pysetupdi installed.
        from bless import (BlessServer, GATTAttributePermissions,
                           GATTCharacteristicProperties)

        self._server = BlessServer(name=self._name)
        self._server.read_request_func = self._handle_read
        self._server.write_request_func = self._handle_write

        await self._server.add_new_service(SERVICE_UUID)
        await self._server.add_new_characteristic(
            SERVICE_UUID, S2C_UUID,
            GATTCharacteristicProperties.notify,
            None,
            GATTAttributePermissions.readable,
        )
        await self._server.add_new_characteristic(
            SERVICE_UUID, C2S_UUID,
            GATTCharacteristicProperties.write,
            None,
            GATTAttributePermissions.writeable | GATTAttributePermissions.readable,
        )

        await self._server.start()
        await asyncio.sleep(1.0)  # let CoreBluetooth finish registering the GATT table
        log.info(f'BLE server advertising as "{self._name}"')

    async def stop(self):
        if self._server:
            await self._server.stop()

    async def send(self, data: bytes):
        if not self._server:
            return
        char = self._server.get_characteristic(S2C_UUID)
        char.value = bytearray(data)
        self._server.update_value(SERVICE_UUID, S2C_UUID)

    def _handle_read(self, characteristic, **_) -> bytearray:
        return characteristic.value or bytearray()

    def _handle_write(self, characteristic, value, **_):
        if str(characteristic.uuid).lower() == C2S_UUID.lower():
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(self._dispatch(bytes(value)), loop)

    async def _dispatch(self, data: bytes):
        self._on_receive(data)


class BLEClient:
    """Central role: scans, connects, writes to server, receives notifications."""

    def __init__(self, peer_name: str, on_receive: OnReceive):
        self._peer_name = peer_name
        self._on_receive = on_receive
        self._client: Optional[BleakClient] = None
        self._connected = asyncio.Event()

    async def connect(self, timeout: float = 30.0):
        log.info(f'Scanning for "{self._peer_name}"...')
        device = await BleakScanner.find_device_by_name(self._peer_name, timeout=timeout)
        if device is None:
            raise RuntimeError(f'BLE device "{self._peer_name}" not found after {timeout}s')

        for attempt in range(5):
            try:
                await self._try_connect(device)
                return
            except OSError as e:
                log.warning(f'Connection attempt {attempt + 1}/5 failed ({e}), reconnecting...')
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(3.0)
        raise RuntimeError(f'Could not establish stable BLE connection after 5 attempts')

    async def _try_connect(self, device):
        self._client = BleakClient(device, disconnected_callback=self._on_disconnect)
        await self._client.connect()
        # bless/CoreBluetooth fires one or more services-changed events after connection
        # which invalidates the WinRT GATT objects on Windows. Wait for them to settle.
        await asyncio.sleep(6.0)

        s2c_char = next(
            (c for s in self._client.services
               for c in s.characteristics
               if c.uuid.lower() == S2C_UUID.lower()),
            None,
        )
        if s2c_char is None:
            available = [c.uuid for s in self._client.services for c in s.characteristics]
            raise RuntimeError(f'S2C characteristic not found. Available: {available}')

        await self._client.start_notify(s2c_char.uuid, self._on_notify)
        self._connected.set()
        log.info(f'Connected to "{self._peer_name}"')

    async def disconnect(self):
        if self._client and self._client.is_connected:
            await self._client.disconnect()

    async def send(self, data: bytes):
        await self._connected.wait()
        # BLE write_gatt_char handles chunking at L2CAP level if MTU is negotiated,
        # but we stay within CHUNK_PAYLOAD_SIZE ourselves to be safe.
        await self._client.write_gatt_char(C2S_UUID, data, response=True)

    def _on_notify(self, _sender, data: bytearray):
        self._on_receive(bytes(data))

    def _on_disconnect(self, _client):
        self._connected.clear()
        log.warning(f'Disconnected from "{self._peer_name}" — will not auto-reconnect')
