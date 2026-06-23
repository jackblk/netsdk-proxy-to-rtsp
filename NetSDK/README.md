# NetSDK — Dahua Network SDK (download separately)

This directory holds the **Dahua NetSDK**, which this project depends on but
**does not bundle**. The SDK is proprietary software owned by *Zhejiang Dahua
Technology Co., Ltd.* and is **not redistributable** under its license, so you
must download it yourself.

> Only `README.md` and `setup.sh` are committed to git. Everything else here
> (the SDK files and the `.whl`) is git-ignored.

## Setup

1. **Download** the Linux NetSDK from Dahua (you must accept their Software
   License Agreement to download):
   <https://previous-depp.dahuasecurity.com/integration/guide/download/sdk>

   Pick the Python Linux64 package — named `General_NetSDK_Eng_Python_linux64_IS_*`.
   This project was built against
   **`General_NetSDK_Eng_Python_linux64_IS_V3.060.0000003.0.R.251201`**
   (newer versions should work too). Unpack it to find the wheel inside.

2. **Paste** the wheel file into this directory (`./NetSDK/`):

   ```
   NetSDK-2.0.0.1-py3-none-linux_x86_64.whl
   ```

3. **Run** the setup script — it extracts the wheel into place and verifies the result:

   ```bash
   ./NetSDK/setup.sh
   ```

That's it. The relay finds the SDK by putting the repo root on `sys.path`
(see `relay/__init__.py`), so `from NetSDK.NetSDK import NetClient` resolves.

## Expected layout (check at a glance)

After `setup.sh` runs, this directory should look like:

```
NetSDK/
├── README.md                 # this file        (committed)
├── setup.sh                  # extractor         (committed)
├── NetSDK-2.0.0.1-...whl      # you paste this    (git-ignored)
├── NetSDK.py                 # ┐
├── SDK_Callback.py           # │ from the wheel   (git-ignored)
├── SDK_Enum.py               # │
├── SDK_Struct.py             # ┘
└── Libs/
    └── linux64/              # native libraries   (git-ignored)
        ├── libdhnetsdk.so
        ├── libdhconfigsdk.so
        ├── libavnetsdk.so
        ├── libplay.so
        ├── libStreamConvertor.so
        ├── libRenderEngine.so
        ├── libInfra.so
        ├── libcrypto.so
        ├── libssl.so
        └── ImageAlg.so
```

## License

The NetSDK is proprietary and subject to Dahua's own license terms, which you
accept when you download it. Please read and comply with those terms (and any
applicable export-control laws) yourself. Because it is not freely
redistributable, it is not included in this repository and is not covered by
this project's MIT license. See [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
