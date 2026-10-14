"""Windows current-user DPAPI storage for an exact Open WebUI server URL.

Only encrypted blobs reach disk. This store deliberately does not use the
preferences directory override, which might point into a shared project.
"""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from . import core


_MAGIC = b"HOULM-DPAPI\x01\x00"
_MAX_FILE_BYTES = 128 * 1024
_MAX_KEY_CHARS = 8192
_STORAGE_ERROR = "The saved login could not be accessed securely."
_INVALID_ERROR = "The saved login is invalid. Enter your API key again."


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _server(value):
    try:
        value = core.normalize_server_url(value)
        if len(value) > 4096:
            raise ValueError()
        value.encode("utf-8")
        return value
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Enter a valid Open WebUI server URL.") from None


def _key(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= _MAX_KEY_CHARS
            or any(ord(char) <= 32 or ord(char) > 126 for char in value)):
        raise ValueError("Enter a valid API key without spaces or line breaks.")
    return value


def _credentials_dir():
    base = os.environ.get("LOCALAPPDATA")
    if not base or not Path(base).is_absolute():
        raise OSError(_STORAGE_ERROR)
    return Path(base) / "Hou-lm" / "credentials"


def _path(server):
    return _credentials_dir() / (hashlib.sha256(server.encode("utf-8")).hexdigest() + ".dpapi")


def _entropy(server):
    return hashlib.sha256(b"Hou-lm credential v1\x00" + server.encode("utf-8")).digest()


def _crypt(data, entropy, decrypt=False):
    """Use DPAPI's default current-user scope; never LOCAL_MACHINE scope."""
    if sys.platform != "win32":
        raise OSError("Saving login credentials requires Windows DPAPI.")
    input_buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(entropy)
    input_blob = _DataBlob(len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy_blob = _DataBlob(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    local_free = None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        local_free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        operation.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                              ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DataBlob)]
        operation.restype = ctypes.c_int
        # CRYPTPROTECT_UI_FORBIDDEN: never open a Windows prompt from Houdini.
        if not operation(ctypes.byref(input_blob), None, ctypes.byref(entropy_blob),
                         None, None, 0x1, ctypes.byref(output_blob)):
            raise OSError(_STORAGE_ERROR)
        if not output_blob.pbData or not 0 < output_blob.cbData <= _MAX_FILE_BYTES:
            raise OSError(_STORAGE_ERROR)
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        raise OSError(_STORAGE_ERROR) from None
    finally:
        # Python strings/bytes themselves cannot be reliably zeroed. Clear the
        # extra native copies, especially DPAPI's decrypted output allocation.
        ctypes.memset(input_buffer, 0, ctypes.sizeof(input_buffer))
        if output_blob.pbData and local_free is not None:
            if decrypt:
                ctypes.memset(output_blob.pbData, 0, output_blob.cbData)
            local_free(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def load_key(server_url):
    """Return the exact server's key, or an empty string when none is saved."""
    server = _server(server_url)
    try:
        with _path(server).open("rb") as source:
            encrypted = source.read(_MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return ""
    except OSError:
        raise OSError(_STORAGE_ERROR) from None
    if len(encrypted) > _MAX_FILE_BYTES or not encrypted.startswith(_MAGIC):
        raise ValueError(_INVALID_ERROR)
    plaintext = _crypt(encrypted[len(_MAGIC):], _entropy(server), decrypt=True)
    try:
        envelope = json.loads(plaintext.decode("utf-8"))
        if (not isinstance(envelope, dict) or set(envelope) != {"version", "server", "key"}
                or type(envelope["version"]) is not int or envelope["version"] != 1
                or envelope["server"] != server):
            raise ValueError()
        return _key(envelope["key"])
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError(_INVALID_ERROR) from None


def save_key(server_url, key):
    """Atomically replace one server's saved key with a current-user DPAPI blob."""
    server, key = _server(server_url), _key(key)
    plaintext = json.dumps({"version": 1, "server": server, "key": key},
                           ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    encrypted = _MAGIC + _crypt(plaintext, _entropy(server))
    if len(encrypted) > _MAX_FILE_BYTES:
        raise OSError(_STORAGE_ERROR)
    temporary = None
    try:
        destination = _path(server)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="login-", suffix=".tmp", dir=str(destination.parent))
        with os.fdopen(descriptor, "wb") as output:
            output.write(encrypted)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        temporary = None
    except OSError:
        raise OSError(_STORAGE_ERROR) from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass  # A failed cleanup leaves only ciphertext, never a key.


def delete_key(server_url):
    """Forget this server's saved key; an already absent key is harmless."""
    server = _server(server_url)
    try:
        _path(server).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        raise OSError(_STORAGE_ERROR) from None
