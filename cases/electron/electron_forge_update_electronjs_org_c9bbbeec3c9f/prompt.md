Support MSIX builds for Electron auto updates.

Ensure `maker-msix` output filenames include the app version, target platform, and target architecture. This is necessary for update.electronjs.org support.

Add support for MSIX builds to the autoUpdater service.

The service now supports these path formats:

```text
/:owner/:repo/:platform/:format/:version
/:owner/:repo/:platform-:arch/:format/:version
```

where `format` is one of `squirrel` or `msix`. Existing URLs without a format continue to use Squirrel, and `squirrel` may also be specified explicitly.

MSIX is supported for Windows x64 and arm64; `win32` without an architecture defaults to x64. macOS and ia32 (32-bit) MSIX are unsupported. MSIX update assets are `.msix` files whose names identify the `win32-x64` or `win32-arm64` target.

MSIX follows the Squirrel.Mac JSON updater feed format and does not use the `RELEASES` endpoint.

Auto-append the `/msix/` URL fragment when `process.windowsStore` is true.
