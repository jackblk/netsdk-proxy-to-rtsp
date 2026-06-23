# Third-Party Notices

This project is a thin wrapper around third-party software. The wrapper code is
MIT-licensed (see [LICENSE](LICENSE)); the components below are **not** and are
**not redistributed** in this repository.

## Dahua NetSDK (`NetSDK/`) — proprietary, NOT included

The `NetSDK/` directory (Python bindings and `Libs/linux64/*.so`) is the
**Dahua Network SDK**, proprietary software owned by
**Zhejiang Dahua Technology Co., Ltd.**

- **Not bundled here.** It is git-ignored and must be obtained directly from Dahua.
- **License:** proprietary, subject to Dahua's own license terms (which you accept
  when you download it). Because it is not freely redistributable, it is not
  included here and is not covered by this project's MIT license.
- **Where to get it & how to install it:** see [NetSDK/README.md](NetSDK/README.md).

Please review Dahua's license terms and any applicable export-control laws yourself
before downloading and using the SDK; this project makes no representations about them.

## Other bundled-at-build-time components

- **MediaMTX** (downloaded in the Docker image): MIT License —
  https://github.com/bluenviron/mediamtx
- **FFmpeg** (system package in the Docker image): LGPL/GPL depending on build —
  https://ffmpeg.org
