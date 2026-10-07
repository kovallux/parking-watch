from pathlib import Path
import subprocess


def keychain_get(service, account):
    try:
        result = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service,
                                 "-a", account, "-w"], capture_output=True, text=True,
                                timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Credential unavailable in macOS Keychain") from None
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError("Credential unavailable in macOS Keychain")
    return result.stdout.strip()


def keychain_set(service, account, secret):
    # Security.framework avoids requiring Swift/Xcode on the destination Mac.
    import ctypes
    try:
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        find = security.SecKeychainFindGenericPassword
        find.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
                         ctypes.c_uint32, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.POINTER(ctypes.c_void_p)]
        find.restype = ctypes.c_int32
        add = security.SecKeychainAddGenericPassword
        add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
                        ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p,
                        ctypes.POINTER(ctypes.c_void_p)]
        add.restype = ctypes.c_int32
        modify = security.SecKeychainItemModifyAttributesAndData
        modify.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p]
        modify.restype = ctypes.c_int32
        core.CFRelease.argtypes = [ctypes.c_void_p]
        encoded_service, encoded_account, encoded_secret = (value.encode("utf-8")
                                                            for value in [service, account, secret])
        item = ctypes.c_void_p()
        result = find(None, len(encoded_service), encoded_service, len(encoded_account),
                      encoded_account, None, None, ctypes.byref(item))
        try:
            if result == 0:
                result = modify(item, None, len(encoded_secret), encoded_secret)
            elif result == -25300:
                result = add(None, len(encoded_service), encoded_service, len(encoded_account),
                             encoded_account, len(encoded_secret), encoded_secret, ctypes.byref(item))
            if result == 0:
                _allow_cli_reader(security, core, item)
        finally:
            if item.value:
                core.CFRelease(item)
    except Exception:
        raise ValueError("Credential could not be saved in macOS Keychain") from None
    if result != 0:
        raise ValueError("Credential could not be saved in macOS Keychain")


def _allow_cli_reader(security, core, item):
    import ctypes
    create_app = security.SecTrustedApplicationCreateFromPath
    create_app.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    create_app.restype = ctypes.c_int32
    create_access = security.SecAccessCreate
    create_access.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    create_access.restype = ctypes.c_int32
    set_access = security.SecKeychainItemSetAccess
    set_access.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    set_access.restype = ctypes.c_int32
    core.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    core.CFStringCreateWithCString.restype = ctypes.c_void_p
    core.CFArrayCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, ctypes.c_void_p]
    core.CFArrayCreate.restype = ctypes.c_void_p
    apps, references = [], []
    try:
        for path in [None, b"/usr/bin/security"]:
            app = ctypes.c_void_p()
            if create_app(path, ctypes.byref(app)) != 0:
                raise ValueError("Keychain access configuration failed")
            apps.append(app.value)
            references.append(app.value)
        values = (ctypes.c_void_p * len(apps))(*apps)
        callbacks = ctypes.c_void_p.in_dll(core, "kCFTypeArrayCallBacks")
        array = core.CFArrayCreate(None, values, len(apps), ctypes.byref(callbacks))
        descriptor = core.CFStringCreateWithCString(None, b"Parking Watch", 0x08000100)
        references.extend([array, descriptor])
        access = ctypes.c_void_p()
        if create_access(descriptor, array, ctypes.byref(access)) != 0:
            raise ValueError("Keychain access configuration failed")
        references.append(access.value)
        if set_access(item, access) != 0:
            raise ValueError("Keychain access configuration failed")
    finally:
        for reference in reversed(references):
            if reference:
                core.CFRelease(reference)
