import ctypes
from ctypes import wintypes


winmm = ctypes.WinDLL("winmm")
mciSendStringW = winmm.mciSendStringW
mciSendStringW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HWND]
mciSendStringW.restype = wintypes.UINT

mciGetErrorStringW = winmm.mciGetErrorStringW
mciGetErrorStringW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.UINT]
mciGetErrorStringW.restype = wintypes.BOOL


def _mci(cmd: str) -> str:
    buf = ctypes.create_unicode_buffer(4096)
    code = mciSendStringW(cmd, buf, len(buf), 0)
    if code != 0:
        err = ctypes.create_unicode_buffer(512)
        if mciGetErrorStringW(code, err, len(err)):
            raise RuntimeError(f"MCI error {code}: {err.value} | cmd={cmd}")
        raise RuntimeError(f"MCI error {code} | cmd={cmd}")
    return buf.value.strip()


class MciWavePlayer:
    def __init__(self) -> None:
        self.alias = "piano_wav"
        self.opened = False
        self.path = ""
        self._speed_supported: bool | None = None

    def open(self, wav_path: str) -> None:
        self.close()
        self.path = wav_path
        _mci(f'open "{wav_path}" type waveaudio alias {self.alias}')
        _mci(f"set {self.alias} time format milliseconds")
        self.opened = True
        self._speed_supported = None

    def close(self) -> None:
        if self.opened:
            try:
                _mci(f"close {self.alias}")
            finally:
                self.opened = False
                self.path = ""
                self._speed_supported = None

    def play(self, start_ms: int = 0) -> None:
        if not self.opened:
            raise RuntimeError("player not opened")
        start_ms = max(0, int(start_ms))
        _mci(f"play {self.alias} from {start_ms}")

    def pause(self) -> None:
        if self.opened:
            _mci(f"pause {self.alias}")

    def resume(self) -> None:
        if self.opened:
            _mci(f"resume {self.alias}")

    def stop(self) -> None:
        if self.opened:
            _mci(f"stop {self.alias}")

    def seek(self, pos_ms: int) -> None:
        if not self.opened:
            return
        pos_ms = max(0, int(pos_ms))
        _mci(f"seek {self.alias} to {pos_ms}")

    def position_ms(self) -> int:
        if not self.opened:
            return 0
        v = _mci(f"status {self.alias} position")
        try:
            return int(v)
        except Exception:
            return 0

    def length_ms(self) -> int:
        if not self.opened:
            return 0
        v = _mci(f"status {self.alias} length")
        try:
            return int(v)
        except Exception:
            return 0

    def state(self) -> str:
        if not self.opened:
            return "closed"
        v = _mci(f"status {self.alias} mode")
        return v.lower()

    def set_speed(self, percent: int) -> None:
        if not self.opened:
            return False
        percent = int(percent)
        if percent <= 0:
            percent = 100
        if self._speed_supported is False:
            return False
        try:
            _mci(f"set {self.alias} speed {percent}")
            self._speed_supported = True
            return True
        except Exception:
            self._speed_supported = False
            return False

    def speed_supported(self) -> bool:
        if not self.opened:
            return False
        if self._speed_supported is None:
            self._speed_supported = self.set_speed(100)
        return bool(self._speed_supported)
