# config/

Runtime config for the relay. This directory is mounted into the container
(`./config:/app/config`), so edits on the host take effect without a rebuild.

Contents (both generated, both git-ignored):

- **`streams.yml`** — the stream list. Generate/refresh it with:
  ```bash
  docker compose run --rm relay parse     # or: uv run python -m relay parse
  ```
  `parse` is non-destructive: it adds newly-found streams, marks invalid ones
  `enable: false`, and refreshes `metadata`, but never overwrites your `name` or
  `enable` edits on a still-valid stream. Edit it to choose which streams go live
  and to rename RTSP paths, then `docker compose up`.

- **`output.m3u8`** — written by `relay run`: a playlist of every stream it
  published, with ready-to-open viewer URLs. Open it in VLC
  (`vlc config/output.m3u8`) to watch all streams at once instead of copy-pasting
  URLs from the logs.

With `ON_DEMAND=true` in `.env`, the enabled streams become lazy — each is pulled from
the device only while a viewer is connected (and torn down ~10s after the last leaves)
instead of running continuously. `output.m3u8` still lists every enabled stream; opening
one starts it.

Only this README is committed — it keeps the directory present so the
`./config` bind mount has somewhere to land on a fresh checkout.
