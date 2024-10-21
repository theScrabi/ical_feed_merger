#!/usr/bin/python3

# ICS Feed Merger
#
# @author Christian Schabesberger
#
# Copyright (C) Christian Schabesberger 2024


from flask import Flask, Response, request
from typing import List, Final
import requests
import re
import os


public_host:Final = os.environ.get("GLOBAL_IFM_HOST", "localhost:5000")

app = Flask(__name__)

def get_urls(request) -> List[str]:
    i = 0
    urls:List[str] = []
    while True:
        p = request.args.get(f'p{i}')
        if p is None:
            break
        urls.append(p)
        i += 1
    return urls

def load_ics(url:str) -> str:
    r = requests.get(re.sub(r'^webcal', 'https', url))
    return r.text

def get_all_calendars(urls:List[str]) -> List[str]:
    calendars:List[str] = []
    for url in urls:
        ics = load_ics(url)
        calendars.append(ics)
    return calendars

def merge_calendars(calendars:List[str]) -> str:
    stripped_calendars = map(lambda c: c.replace('BEGIN:VCALENDAR', '').replace('END:VCALENDAR', ''), calendars)
    merged_calendar = ''.join(list(stripped_calendars))
    return "BEGIN:VCALENDAR\n" + merged_calendar + "END:VCALENDAR\n"


def serve_merged_callendar(calendars: List[str]) -> Response:
    merged_calendar = merge_calendars(calendars)
    resp = Response(merged_calendar, mimetype="text/calendar")
    return resp


def serve_config_page() -> Response:
    config_page_file = open('config_page.html', 'r')
    config_page = config_page_file.read()
    config_page = config_page.replace("$$GLOBAL_IFM_HOST$$", public_host)
    return Response(config_page)


@app.route("/")
def hello_world():
    calendars = get_all_calendars(get_urls(request))
    if len(calendars) == 0:
        resp = serve_config_page()
    else:
        resp = serve_merged_callendar(calendars)
    return resp
