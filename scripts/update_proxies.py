#!/usr/bin/env python3
"""
ObedTech Free Proxies - Auto Updater
Scrapes, validates, and saves proxies to /files/
"""

import asyncio
import aiohttp
import json
import os
import re
import time
import random
from datetime import datetime, timezone
from collections import defaultdict

TIMEOUT        = 10
MAX_CONCURRENT = 200
TEST_URL       = "http://httpbin.org/ip"
FILES_DIR      = os.path.join(os.path.dirname(__file__), "..", "files")

SOURCES = {
    "http": [
        "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/http.txt",
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt",
    ],
    "socks4": [
        "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt",
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks4/data.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks4.txt",
    ],
    "socks5": [
        "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt",
    ],
}

IP_INFO_URL = "http://ip-api.com/batch"
PROXY_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}$")

def parse_proxies(text):
    return [l.strip() for l in text.splitlines() if PROXY_RE.match(l.strip())]

async def fetch_source(session, url):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            return parse_proxies(await r.text())
    except Exception:
        return []

async def collect_all_proxies():
    print("📥 Collecting proxies...")
    all_proxies = {k: set() for k in SOURCES}
    async with aiohttp.ClientSession() as session:
        tasks = [(p, fetch_source(session, u)) for p, urls in SOURCES.items() for u in urls]
        results = await asyncio.gather(*[t for _, t in tasks])
        for (proto, _), proxies in zip(tasks, results):
            all_proxies[proto].update(proxies)
    return {k: list(v) for k, v in all_proxies.items()}

async def check_http_proxy(session, proxy):
    start = time.monotonic()
    try:
        async with session.get(TEST_URL, proxy=f"http://{proxy}",
                               timeout=aiohttp.ClientTimeout(total=TIMEOUT), ssl=False) as r:
            if r.status == 200:
                return (proxy, round(time.monotonic() - start, 2))
    except Exception:
        pass
    return None

async def check_socks_proxy(proxy, _):
    ip, port = proxy.rsplit(":", 1)
    start = time.monotonic()
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(ip, int(port)), timeout=TIMEOUT)
        w.close()
        await w.wait_closed()
        return (proxy, round(time.monotonic() - start, 2))
    except Exception:
        return None

async def validate_proxies(raw):
    print("✅ Validating proxies...")
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    valid = {k: [] for k in raw}

    async with aiohttp.ClientSession() as session:
        http_list = random.sample(raw["http"], min(2000, len(raw["http"])))
        async def chk_http(p):
            async with sem: return await check_http_proxy(session, p)
        for r in await asyncio.gather(*[chk_http(p) for p in http_list]):
            if r: valid["http"].append({"proxy": r[0], "response_time": r[1]})

    for proto in ("socks4", "socks5"):
        sample = random.sample(raw[proto], min(1000, len(raw[proto])))
        async def chk(p, pt=proto):
            async with sem: return await check_socks_proxy(p, 0)
        for r in await asyncio.gather(*[chk(p) for p in sample]):
            if r: valid[proto].append({"proxy": r[0], "response_time": r[1]})

    print(f"   HTTP: {len(valid['http'])} | SOCKS4: {len(valid['socks4'])} | SOCKS5: {len(valid['socks5'])}")
    return valid

async def enrich_with_geo(proxies_flat):
    print(f"🌍 Enriching geo info for {len(proxies_flat)} proxies...")
    ip_to_info = {}
    ips = list({p.split(":")[0] for p in proxies_flat})
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(ips), 100):
            try:
                async with session.post(IP_INFO_URL, json=ips[i:i+100],
                                        timeout=aiohttp.ClientTimeout(total=30)) as r:
                    for item in await r.json():
                        if item.get("status") == "success":
                            ip_to_info[item["query"]] = item
            except Exception:
                pass
            await asyncio.sleep(1.4)
    print(f"   Geo enriched: {len(ip_to_info)} IPs")
    return ip_to_info

def build_metadata(valid, geo):
    metadata = []
    for proto, entries in valid.items():
        for e in entries:
            ip, port = e["proxy"].split(":")[0], e["proxy"].split(":")[1]
            g = geo.get(ip, {})
            metadata.append({
                "status": "success" if g else "unknown",
                "country": g.get("country", "Unknown"),
                "countryCode": g.get("countryCode", "XX"),
                "region": g.get("region", ""),
                "regionName": g.get("regionName", ""),
                "city": g.get("city", ""),
                "zip": g.get("zip", ""),
                "lat": g.get("lat", 0),
                "lon": g.get("lon", 0),
                "timezone": g.get("timezone", ""),
                "isp": g.get("isp", ""),
                "org": g.get("org", ""),
                "as": g.get("as", ""),
                "query": ip,
                "type": proto,
                "port": port,
                "response_time": e["response_time"],
            })
    return metadata

def save_files(valid, metadata):
    print("💾 Saving files...")
    os.makedirs(FILES_DIR, exist_ok=True)
    os.makedirs(os.path.join(FILES_DIR, "countries"), exist_ok=True)
    os.makedirs(os.path.join(FILES_DIR, "metadata"), exist_ok=True)

    pl = lambda e: [x["proxy"] for x in e]

    # Protocol files (simple proxy lists)
    for proto in ("http", "socks4", "socks5"):
        with open(os.path.join(FILES_DIR, f"{proto}.json"), "w") as f:
            json.dump({"proxies": pl(valid[proto])}, f, separators=(",", ":"))

    # Combined
    all_p = pl(valid["http"]) + pl(valid["socks4"]) + pl(valid["socks5"])
    with open(os.path.join(FILES_DIR, "proxies.json"), "w") as f:
        json.dump({"proxies": all_p}, f, separators=(",", ":"))

    # Random sample
    with open(os.path.join(FILES_DIR, "random.json"), "w") as f:
        json.dump({"proxies": random.sample(all_p, min(10, len(all_p)))}, f, separators=(",", ":"))

    # Full metadata
    with open(os.path.join(FILES_DIR, "metadata.json"), "w") as f:
        json.dump(metadata, f, separators=(",", ":"))

    # Per-protocol metadata
    for proto in ("http", "socks4", "socks5"):
        with open(os.path.join(FILES_DIR, "metadata", f"{proto}-metadata.json"), "w") as f:
            json.dump([m for m in metadata if m["type"] == proto], f, separators=(",", ":"))

    # Per-country files — RICH FORMAT matching giftedtech style
    by_country = defaultdict(list)
    for m in metadata:
        code = m.get("countryCode") or "XX"
        by_country[code].append(m)

    for code, entries in by_country.items():
        country_name = entries[0].get("country", "Unknown")
        rich_proxies = []
        for m in entries:
            rich_proxies.append({
                "status": m["status"],
                "country": m["country"],
                "countryCode": m["countryCode"],
                "region": m["region"],
                "regionName": m["regionName"],
                "city": m["city"],
                "zip": m["zip"],
                "lat": m["lat"],
                "lon": m["lon"],
                "timezone": m["timezone"],
                "isp": m["isp"],
                "org": m["org"],
                "as": m["as"],
                "query": m["query"],
                "type": m["type"],
                "port": m["port"],
                "response_time": m["response_time"],
            })
        country_data = {
            "countryCode": code,
            "country": country_name,
            "count": len(rich_proxies),
            "proxies": rich_proxies,
        }
        with open(os.path.join(FILES_DIR, "countries", f"{code}.json"), "w") as f:
            json.dump(country_data, f, separators=(",", ":"))

    # Timestamp
    now = datetime.now(timezone.utc)
    with open(os.path.join(FILES_DIR, "timestamp.json"), "w") as f:
        json.dump({
            "updated": now.strftime("%A %d-%m-%Y %H:%M:%S EAT"),
            "unix": int(now.timestamp())
        }, f, separators=(",", ":"))

    h, s4, s5, c = len(valid["http"]), len(valid["socks4"]), len(valid["socks5"]), len(by_country)
    total = len(all_p)
    print(f"\n🎉 Done! Total: {total} | HTTP: {h} | SOCKS4: {s4} | SOCKS5: {s5} | Countries: {c}")
    return total, h, s4, s5, c

def update_readme(total, h, s4, s5, c):
    now = datetime.now(timezone.utc).strftime("%A %d-%m-%Y %H:%M:%S EAT")
    with open(os.path.join(FILES_DIR, "..", "README.md"), "w") as f:
        f.write(
            f"# free-proxies\n\n"
            f"> **{total} working validated proxies** last updated on **{now}**\n\n"
            f"Free, continuously validated **HTTP**, **SOCKS4** and **SOCKS5** proxies tested live "
            f"and organised by protocol and country. Updated every **30 mins**.\n\n"
            f"![HTTP](https://img.shields.io/badge/HTTP-{h}-blue?style=flat-square) "
            f"![SOCKS4](https://img.shields.io/badge/SOCKS4-{s4}-green?style=flat-square) "
            f"![SOCKS5](https://img.shields.io/badge/SOCKS5-{s5}-purple?style=flat-square) "
            f"![Countries](https://img.shields.io/badge/Countries-{c}-orange?style=flat-square)\n\n"
            f"🌐 Access Proxies [Here](https://proxies.obedtech.top)\n"
        )
    print("📝 README updated")

async def main():
    print("🚀 ObedTech Proxy Updater starting...\n")
    raw = await collect_all_proxies()
    valid = await validate_proxies(raw)
    all_valid = [e["proxy"] for v in valid.values() for e in v]
    geo = await enrich_with_geo(all_valid)
    metadata = build_metadata(valid, geo)
    total, h, s4, s5, c = save_files(valid, metadata)
    update_readme(total, h, s4, s5, c)

if __name__ == "__main__":
    asyncio.run(main())
