#!/usr/bin/env python3
"""
Yeni yayınlanan blog yazısını destek.visibiy.com → Yanar Şömine → "Yapılan İşlemler" listesine
"Şömine Rehberi" marka grubu altında tamamlandı (tikli) bir madde olarak ekler.

Kullanım: python scripts/notify_support.py <slug> [kategori-etiketi] [--env-file yol]
Env: INTERNAL_API_SECRET   (destek panelindeki x-internal-secret ile aynı)
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORT_URL = "https://destek.visibiy.com/api/internal/add-work-item"
CUSTOMER_ID = 3  # destek.visibiy.com — Yanar Şömine müşteri kaydı (sominerehberi.com bu markanın yan sitesi)
BRAND = "Şömine Rehberi"  # panelde madde bu marka grubunun altında listelenir (Marka Listesi ile birebir aynı yazılmalı)
SITE = "https://sominerehberi.com"
UA = "Mozilla/5.0 (compatible; SomineRehberiBot/1.0)"


def load_env(path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--env-file" in sys.argv:
        load_env(sys.argv[sys.argv.index("--env-file") + 1])
        args = [a for a in args if a != sys.argv[sys.argv.index("--env-file") + 1]]
    slug = args[0]
    label = args[1] if len(args) > 1 else "Blog"
    text = (ROOT / "src" / "content" / "yazilar" / f"{slug}.md").read_text(encoding="utf-8")
    title = json.loads(re.search(r"^title:\s*(.+)$", text, re.M).group(1))
    url = f"{SITE}/yazi/{slug}"

    # Vercel yayına alana kadar bekle (en fazla ~4 dk); alamazsa yine de ekle
    live = False
    for _ in range(24):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                live = r.status == 200
        except Exception:  # noqa: BLE001
            live = False
        if live:
            break
        time.sleep(10)

    payload = {
        "customerId": CUSTOMER_ID,
        "brand": BRAND,
        "title": f"{label} blogu yayınlandı: {title}",
        "notes": url + ("" if live else " (yayına alınma doğrulanamadı)"),
        "completed": True,
    }
    req = urllib.request.Request(
        SUPPORT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-internal-secret": os.environ["INTERNAL_API_SECRET"], "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("Yapılanlara eklendi:", r.read().decode("utf-8"), "|", payload["title"])
    except urllib.error.HTTPError as e:
        sys.exit(f"Destek paneli hata verdi: {e.code} {e.read().decode('utf-8')[:200]}")


if __name__ == "__main__":
    main()
