# EchoDrop

EchoDrop is a local web interface for inspecting Echo360 recordings and downloading selected video/audio streams with `yt-dlp`.

## Run locally

1. Install `yt-dlp`:

   ```sh
   python3 -m pip install -U yt-dlp
   ```

   Install `ffmpeg` using your operating system's package manager as well.

2. Start the local server:

   ```sh
   python3 server.py
   ```

   Start the server with the same Python environment used to install `yt-dlp`.
   If `yt-dlp` is not on your `PATH`, provide its executable explicitly:

   ```sh
   ECHODROP_YT_DLP="$HOME/.local/bin/yt-dlp" python3 server.py
   ```

3. Open <http://127.0.0.1:8787/>.

EchoDrop reads the authenticated Flatpak Brave profile automatically. Keep the Echo360 login in Brave's Default profile and, if cookie access is locked, close Brave before loading a section.

Paste a section URL such as `https://echo360.org.uk/section/<section-id>/home`. EchoDrop calls the authenticated syllabus endpoint once and lists only lessons with an available video media entry; scheduled lessons with no recorded media are omitted. Loading a section again replaces the previous list. Clicking a lecture then resolves its video/audio HLS streams, displays each source thumbnail and HLS filename (such as `s1_v` or `s2_av`), and downloads the selected streams into one MP4 with ffmpeg. Equivalent media URLs are normalized and shown only once. URLs are kept in the current browser session only.

The server binds to `127.0.0.1` by default. Override the port or download location with `ECHODROP_PORT` and `ECHODROP_DOWNLOAD_DIR`.

Authentication data is stored in the user's local configuration directory and is not part of the repository. Do not commit cookies, `.env` files, downloaded media, or other local credentials.
