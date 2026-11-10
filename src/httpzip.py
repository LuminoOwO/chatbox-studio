"""Reads single files out of a big ZIP on a web server without downloading all of it
(HTTP range requests). Used to fetch one CTranslate2 ROCm wheel from a ~400 MB archive."""

from __future__ import annotations

import io
import urllib.request
from typing import Callable, Optional

UA = {"User-Agent": "ChatboxStudio-Setup"}


class HttpFile(io.RawIOBase):
    def __init__(self, url: str, timeout: float = 60):
        req = urllib.request.Request(url, method="HEAD", headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            self.url = r.geturl()                 # after redirects (signed download URL)
            self.size = int(r.headers["Content-Length"])
            if r.headers.get("Accept-Ranges", "bytes") == "none":
                raise OSError("server does not support ranges")
        self.pos = 0
        self.timeout = timeout

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.pos = off
        elif whence == io.SEEK_CUR:
            self.pos += off
        else:
            self.pos = self.size + off
        return self.pos

    def readinto(self, b):
        if self.pos >= self.size:
            return 0
        end = min(self.size, self.pos + len(b)) - 1
        req = urllib.request.Request(self.url, headers={**UA, "Range": f"bytes={self.pos}-{end}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = r.read()
        n = len(data)
        b[:n] = data
        self.pos += n
        return n


def extract(url: str, pick: Callable[[str], bool], dest_dir: str,
            progress: Optional[Callable[[int], None]] = None) -> str:
    """Saves the first archive member for which pick(name) is true into dest_dir."""
    import os
    import zipfile
    raw = HttpFile(url)
    f = io.BufferedReader(raw, buffer_size=4 << 20)
    with zipfile.ZipFile(f) as z:
        info = next((i for i in z.infolist() if pick(i.filename)), None)
        if info is None:
            raise FileNotFoundError("no matching file in " + url)
        out = os.path.join(dest_dir, os.path.basename(info.filename))
        done = 0
        with z.open(info) as src, open(out, "wb") as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
                done += len(chunk)
                if progress:
                    progress(int(done * 100 / max(1, info.file_size)))
    return out
