"""Minimal CurrentUser Windows DPAPI wrapper; no hardware or custom encryption."""
import ctypes
from ctypes import wintypes
import os

from .errors import AccessDenied


class WindowsDPAPI:
    def _transform(self, data: bytes, *, encrypt: bool) -> bytes:
        if os.name != "nt":
            raise AccessDenied("WINDOWS_REQUIRED")

        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        source = ctypes.create_string_buffer(data)
        incoming = Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
        outgoing = Blob()
        function = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        try:
            # CRYPTPROTECT_UI_FORBIDDEN only; no LOCAL_MACHINE scope or stored key.
            if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
                raise AccessDenied("DECRYPT_FAILED" if not encrypt else "STATE_UNAVAILABLE")
            return ctypes.string_at(outgoing.data, outgoing.size)
        finally:
            if outgoing.data:
                ctypes.memset(outgoing.data, 0, outgoing.size)
                kernel.LocalFree(outgoing.data)

    def protect(self, data: bytes) -> bytes:
        return self._transform(data, encrypt=True)

    def unprotect(self, data: bytes) -> bytes:
        return self._transform(data, encrypt=False)
