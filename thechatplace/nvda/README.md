# NVDA Controller Client

`nvdaControllerClient.dll` is how The Chat Place speaks through NVDA (#98). NVDA doesn't
install this file, so the app ships it, as other apps for blind users do.

- Source: NV Access, `https://download.nvaccess.org/releases/stable/nvda_2026.2_controllerClient.zip`,
  copied unmodified.
- `x64/` is for the built app and x64 Python; `arm64/` is for native ARM64 Python. The DLL has to
  match the process that loads it, not NVDA. NVDA is reached over local RPC.
- Licence: GNU Lesser General Public License 2.1, in `license.txt`. Copyright NV Access Limited.
  You may replace these files with any build of the same library.

SHA-256:

```
598b7ec3dc469814f571275929f676ce73834c469fbdb359a06fd4db4e0fc866  x64/nvdaControllerClient.dll
6faaa1dd82bae2ae953bd14f5206fef63b8932d9a3cb4578fe027ec794f7918e  arm64/nvdaControllerClient.dll
```

To update: download the newer zip from NV Access, copy the two DLLs and `license.txt` over these,
and update the version and hashes above.
