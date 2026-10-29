#!/usr/bin/python3

# ICS Feed Merger
#
# @author Christian Schabesberger
#
# Copyright (C) Christian Schabesberger 2024


from flask import Flask, Response, request
from ics_filter import FilterError, parse_filter_config, filter_calendar
from urllib.parse import urlparse, urljoin
from typing import List, Final
import urllib3.util.connection
import ipaddress
import requests
import socket
import time
import re
import os


public_host:Final = os.environ.get("GLOBAL_IFM_HOST", "localhost:5000")

MAX_FEEDS:Final = 20
MAX_FEED_SIZE:Final = 5 * 1024 * 1024
MAX_TOTAL_FEED_SIZE:Final = 20 * 1024 * 1024
MAX_REDIRECTS:Final = 5
# Total time budget for fetching all feeds of one request.
# Must stay well below gunicorn's worker timeout.
FETCH_DEADLINE:Final = 15
CONNECT_TIMEOUT:Final = 3
READ_TIMEOUT:Final = 5

app = Flask(__name__)


class FeedError(Exception):
    pass


# --- SSRF protection ---
# Every outgoing connection made by requests/urllib3 goes through
# urllib3.util.connection.create_connection. We resolve the host ourselves,
# refuse any non-public address and then connect to exactly the address we
# checked. Because this happens at connect time it also covers redirects and
# DNS rebinding.

_original_create_connection = urllib3.util.connection.create_connection

# IPv6 ranges that embed an IPv4 address in their last 32 bits. NAT64
# gateways translate 64:ff9b::/96 to the embedded address, so it must be
# checked like that address. ::/96 are the deprecated IPv4-compatible addresses.
EMBEDDED_IPV4_NETWORKS:Final = (ipaddress.IPv6Network('64:ff9b::/96'), ipaddress.IPv6Network('::/96'))

def is_public_ip(ip_str:str) -> bool:
    ip = ipaddress.ip_address(ip_str.split('%')[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif any(ip in network for network in EMBEDDED_IPV4_NETWORKS):
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip.is_global and not ip.is_multicast

def safe_create_connection(address, *args, **kwargs):
    host, port = address
    addr_infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    ips = [info[4][0] for info in addr_infos]
    if len(ips) == 0 or not all(is_public_ip(ip) for ip in ips):
        raise OSError(f"Refusing to connect to non-public address of {host}")
    last_error = None
    for ip in ips:
        try:
            return _original_create_connection((ip, port), *args, **kwargs)
        except OSError as e:
            last_error = e
    raise last_error

urllib3.util.connection.create_connection = safe_create_connection


def get_urls(request) -> List[str]:
    i = 0
    urls:List[str] = []
    while True:
        p = request.args.get(f'p{i}')
        if p is None:
            break
        if i >= MAX_FEEDS:
            raise FeedError(f"Too many feeds, at most {MAX_FEEDS} are supported.")
        urls.append(p)
        i += 1
    return urls

def is_scheme_ok(url:str) -> bool:
    return urlparse(url).scheme in ("http", "https")

def remaining_time(deadline:float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FeedError("Loading the feeds took too long.")
    return remaining

def download_feed(url:str, deadline:float, max_size:int) -> bytes:
    session = requests.Session()
    # don't pick up proxy settings or .netrc credentials from the environment
    session.trust_env = False
    for _ in range(MAX_REDIRECTS + 1):
        if not is_scheme_ok(url):
            raise FeedError(f"Url {url} is not a supported iCal feed.")
        timeout = (min(CONNECT_TIMEOUT, remaining_time(deadline)),
                   min(READ_TIMEOUT, remaining_time(deadline)))
        r = session.get(url, timeout=timeout, stream=True, allow_redirects=False)
        with r:
            if r.is_redirect:
                url = urljoin(url, r.headers['Location'])
                continue
            if r.status_code != 200:
                raise FeedError(f"Url {url} is not a supported iCal feed.")
            content = bytearray()
            for chunk in r.iter_content(chunk_size=64 * 1024):
                content += chunk
                if len(content) > max_size:
                    raise FeedError(f"Url {url} is too large, feeds may be at most "
                                    f"{MAX_FEED_SIZE // 2**20} MB each and {MAX_TOTAL_FEED_SIZE // 2**20} MB in total.")
                remaining_time(deadline)
        return bytes(content)
    raise FeedError(f"Url {url} redirects too often.")

def get_all_calendars(urls:List[str]) -> List[str]:
    deadline = time.monotonic() + FETCH_DEADLINE
    remaining_size = MAX_TOTAL_FEED_SIZE
    calendars:List[str] = []
    for url in urls:
        url = re.sub(r'^webcals?://', 'https://', url)
        try:
            content = download_feed(url, deadline, min(MAX_FEED_SIZE, remaining_size))
        except requests.RequestException:
            # deliberately no details, so this can't be used to probe hosts
            raise FeedError(f"Could not load feed {url}.")
        remaining_size -= len(content)
        # RFC 5545 defines UTF-8 as the default charset
        ics = content.decode('utf-8', errors='replace')
        if not ics.lstrip('﻿ \t\r\n').startswith('BEGIN:VCALENDAR'):
            raise FeedError(f"Url {url} is not a supported iCal feed.")
        calendars.append(ics)
    return calendars

def merge_calendars(calendars:List[str]) -> str:
    stripped_calendars = map(lambda c: c.replace('BEGIN:VCALENDAR\r\n', '').replace('END:VCALENDAR\r\n', '').replace('END:VCALENDAR', ''), calendars)
    merged_calendar = ''.join(list(stripped_calendars))
    return "BEGIN:VCALENDAR\r\n" + merged_calendar + "END:VCALENDAR\r\n"


def serve_merged_callendar(calendars: List[str]) -> Response:
    merged_calendar = merge_calendars(calendars)
    resp = Response(merged_calendar, mimetype="text/calendar")
    return resp


def serve_config_page() -> Response:
    config_page_file = open('config_page.html', 'r')
    config_page = config_page_file.read()
    config_page = config_page.replace("$$GLOBAL_IFM_HOST$$", public_host)
    return Response(config_page)


@app.after_request
def add_security_headers(resp:Response) -> Response:
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


@app.route("/")
def merge_calendars_entry():
    try:
        calendars = get_all_calendars(get_urls(request))
    except FeedError as e:
        return Response(str(e), status=400, mimetype="text/plain")
    if len(calendars) == 0:
        resp = serve_config_page()
    else:
        resp = serve_merged_callendar(calendars)
    return resp


@app.route("/filter")
def filter_calendar_entry():
    src = request.args.get('src')
    if src is None:
        return serve_config_page()
    try:
        # validate the filters before loading anything
        filter_config = parse_filter_config(request.args)
        calendar = get_all_calendars([src])[0]
        filtered_calendar = filter_calendar(calendar, filter_config)
    except (FeedError, FilterError) as e:
        return Response(str(e), status=400, mimetype="text/plain")
    return Response(filtered_calendar, mimetype="text/calendar")
