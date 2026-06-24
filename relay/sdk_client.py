"""Thin wrapper over the Dahua NetSDK for headless live preview."""
import ctypes
import logging
from typing import Callable

import relay  # noqa: F401  (sys.path bootstrap so NetSDK imports resolve)

try:
    from NetSDK.NetSDK import NetClient
    from NetSDK.SDK_Struct import (
        NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY,
        NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY,
    )
    from NetSDK.SDK_Enum import EM_LOGIN_SPAC_CAP_TYPE, EM_REALDATA_FLAG
    from NetSDK.SDK_Callback import fRealDataCallBackEx2, fDisConnect
except ImportError as e:
    raise ImportError(
        "The Dahua NetSDK was not found. It is proprietary and not bundled with "
        "this project. See NetSDK/README.md for how to download it and run "
        "./NetSDK/setup.sh."
    ) from e

log = logging.getLogger(__name__)

# Callback receives raw bytes for a chunk of stream data.
RawDataHandler = Callable[[bytes], None]


class DahuaClient:
    def __init__(self):
        self._sdk = NetClient()           # singleton; loads .so libs
        self._login_id = 0
        self._channel_count = 0
        # Hold strong refs so ctypes callbacks are not garbage-collected.
        self._disconnect_cb = fDisConnect(self._on_disconnect)
        # One callback object, registered for every RealPlay; dispatch by handle.
        self._realdata_cb = fRealDataCallBackEx2(self._raw_trampoline)
        self._handlers: dict[int, RawDataHandler] = {}

    # --- lifecycle ---
    def init(self):
        if self._sdk.InitEx(self._disconnect_cb) != 1:
            raise RuntimeError("NetSDK InitEx failed")
        log.info("NetSDK initialized")

    def login(self, host: str, port: int, username: str, password: str) -> int:
        stu_in = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stu_in.dwSize = ctypes.sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
        stu_in.szIP = host.encode()
        stu_in.nPort = int(port)
        stu_in.szUserName = username.encode()
        stu_in.szPassword = password.encode()
        stu_in.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP
        stu_out = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stu_out.dwSize = ctypes.sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)
        login_id, device_info, err = self._sdk.LoginWithHighLevelSecurity(stu_in, stu_out)
        if login_id == 0:
            raise RuntimeError(f"Login failed: {err}")
        self._login_id = login_id
        self._channel_count = device_info.nChanNum
        log.info("Logged in: login_id=%s channels=%s", login_id, self._channel_count)
        return login_id

    @property
    def channel_count(self) -> int:
        return self._channel_count

    def start_realplay(self, channel: int, play_type: int, on_raw: RawDataHandler) -> int:
        """Start a headless preview session; return its handle. May be called many times."""
        handle = self._sdk.RealPlayEx(self._login_id, channel, 0, play_type)
        if handle == 0:
            raise RuntimeError(f"RealPlayEx failed for channel={channel} play_type={play_type}")
        self._handlers[handle] = on_raw
        ok = self._sdk.SetRealDataCallBackEx2(
            handle, self._realdata_cb, 0, EM_REALDATA_FLAG.RAW_DATA
        )
        if not ok:
            self._sdk.StopRealPlayEx(handle)
            del self._handlers[handle]
            raise RuntimeError("SetRealDataCallBackEx2 failed")
        log.info("RealPlay started: handle=%s channel=%s play_type=%s", handle, channel, play_type)
        return handle

    def stop_realplay(self, handle: int):
        if handle and handle in self._handlers:
            self._sdk.StopRealPlayEx(handle)
            del self._handlers[handle]

    def logout(self):
        if self._login_id:
            self._sdk.Logout(self._login_id)
            self._login_id = 0

    def cleanup(self):
        for handle in list(self._handlers):
            self._sdk.StopRealPlayEx(handle)
        self._handlers.clear()
        self.logout()
        self._sdk.Cleanup()

    # --- ctypes callbacks ---
    def _raw_trampoline(self, lRealHandle, dwDataType, pBuffer, dwBufSize, param, dwUser):
        # dwDataType == 0 => raw (DHAV) stream bytes (video+audio multiplexed).
        if dwDataType != 0 or dwBufSize <= 0:
            return
        handler = self._handlers.get(lRealHandle)
        if handler is None:
            return
        data = ctypes.string_at(pBuffer, dwBufSize)
        try:
            handler(data)
        except Exception:                       # never let an exception cross into the SDK
            log.exception("raw data handler error")

    def _on_disconnect(self, lLoginID, pchDVRIP, nDVRPort, dwUser):
        ip = pchDVRIP.decode(errors="replace") if pchDVRIP else "?"
        log.warning("Device disconnected: %s:%s (SDK will auto-reconnect)", ip, nDVRPort)
