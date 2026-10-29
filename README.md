iCal Calendar feed merger
=========================

This is an iCal Calendar feed merger.
You basically post a bunch of ical feeds you want to merge and then subscribe to the combined feed.

I made this, so it looks a bit cleaner when subscribing to a bunch of iCal feeds.

You can try this out at [icfm.schabi.org](https://icfm.schabi.org).

Feed filter
-----------

You can also filter the events of a single iCal feed under `/filter`.
The whole configuration is part of the URL, so you can subscribe to it directly:

```
https://icfm.schabi.org/filter?src=<url encoded feed url>&d=keep&tz=Europe/Berlin&f0=remove:SUMMARY:contains:Holiday&f1=keep:DTSTART:weekday:SA,SU
```

* `src`: the feed to filter.
* `f0`, `f1`, ...: the filters in the form `action:property:operation:value`.
  The first filter that matches an event decides whether it is kept (`keep`) or removed (`remove`).
  * Text properties (e.g. `SUMMARY`, `DESCRIPTION`, `LOCATION`, `CATEGORIES`): `contains`, `equals` (both case insensitive) or `regex`.
  * `DTSTART` / `DTEND`: `time` (`HH:MM-HH:MM`), `weekday` (`MO,TU,...`), `monthday` (`1,15,20-31`),
    `month` (`1-12`), `yearday` (`1-366`) or `allday`.
* `d`: what happens to events that match no filter, `keep` (default) or `remove`.
* `tz`: the timezone used for date/time filters. Times in UTC or with a known `TZID` are converted to it.

Recurring events are filtered by their first occurrence only.
