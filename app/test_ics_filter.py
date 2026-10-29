#!/usr/bin/python3

# ICS Feed Filter tests
#
# @author Christian Schabesberger
#
# Copyright (C) Christian Schabesberger 2024


import unittest
from ics_filter import FilterError, parse_filter_config, filter_calendar, read_content_lines, parse_content_line, \
    MAX_EVENT_LINES, MAX_FOLDED_LINES


def event(uid:str, *lines:str) -> str:
    return "BEGIN:VEVENT\r\nUID:" + uid + "\r\n" + "".join(l + "\r\n" for l in lines) + "END:VEVENT\r\n"

def calendar(*events:str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n"

def kept_uids(ics:str, **args) -> list:
    result = filter_calendar(ics, parse_filter_config(args))
    return [l.value for l in read_content_lines(result) if l.name == "UID"]


class ParsingTest(unittest.TestCase):
    def test_unfolding(self):
        lines = read_content_lines("SUMMARY:Hello\r\n  World\r\n\tagain\r\nUID:1\r\n")
        self.assertEqual(lines[0].value, "Hello World" + "again")
        self.assertEqual(lines[0].raw_lines, ["SUMMARY:Hello", "  World", "\tagain"])
        self.assertEqual(lines[1].value, "1")

    def test_quoted_params(self):
        name, params, value = parse_content_line('DESCRIPTION;ALTREP="http://x.org/a;b":Text: here')
        self.assertEqual(name, "DESCRIPTION")
        self.assertEqual(params, {"ALTREP": "http://x.org/a;b"})
        self.assertEqual(value, "Text: here")

    def test_trailing_empty_lines_are_dropped(self):
        lines = read_content_lines("UID:1\r\n\r\nUID:2\n\n\r\n")
        self.assertEqual([l.raw_lines for l in lines], [["UID:1"], [""], ["UID:2"]])

    def test_lf_line_endings(self):
        ics = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:1\nSUMMARY:x\nEND:VEVENT\nEND:VCALENDAR"
        self.assertEqual(kept_uids(ics, f0="remove:SUMMARY:equals:x"), [])


class TextFilterTest(unittest.TestCase):
    ics = calendar(
        event("1", "SUMMARY:Team Meeting", "DESCRIPTION:Line one\\nLine\\, two"),
        event("2", "SUMMARY:Lunch", "CATEGORIES:Work,Food"),
        event("3", "SUMMARY:Holiday", "BEGIN:VALARM", "DESCRIPTION:Meeting reminder", "END:VALARM"),
    )

    def test_contains_case_insensitive(self):
        self.assertEqual(kept_uids(self.ics, f0="remove:SUMMARY:contains:meeting"), ["2", "3"])

    def test_equals(self):
        self.assertEqual(kept_uids(self.ics, f0="keep:SUMMARY:equals:lunch", d="remove"), ["2"])

    def test_regex(self):
        self.assertEqual(kept_uids(self.ics, f0="keep:SUMMARY:regex:^(Lunch|Holiday)$", d="remove"), ["2", "3"])

    def test_unescaped_text(self):
        self.assertEqual(kept_uids(self.ics, f0="remove:DESCRIPTION:contains:line, two"), ["2", "3"])
        self.assertEqual(kept_uids(self.ics, f0="remove:DESCRIPTION:regex:one\nLine"), ["2", "3"])

    def test_list_property(self):
        self.assertEqual(kept_uids(self.ics, f0="remove:CATEGORIES:equals:food"), ["1", "3"])

    def test_nested_components_are_ignored(self):
        self.assertEqual(kept_uids(self.ics, f0="remove:DESCRIPTION:contains:reminder"), ["1", "2", "3"])

    def test_missing_property_is_empty(self):
        self.assertEqual(kept_uids(self.ics, f0="remove:LOCATION:equals:"), [])

    def test_value_may_contain_colon(self):
        ics = calendar(event("1", "URL:https://example.org"), event("2", "URL:http://example.org"))
        self.assertEqual(kept_uids(ics, f0="remove:URL:contains:https://"), ["2"])


class DateFilterTest(unittest.TestCase):
    ics = calendar(
        # Monday 2024-01-01 09:30 UTC = 10:30 Europe/Berlin
        event("utc", "DTSTART:20240101T093000Z", "DTEND:20240101T103000Z"),
        # Saturday 2024-03-16 22:00 New York = Sunday 03:00 Berlin
        event("ny", "DTSTART;TZID=America/New_York:20240316T220000"),
        # all-day, Thursday 2024-02-29 (day of year 60)
        event("allday", "DTSTART;VALUE=DATE:20240229"),
        # floating, unknown TZID behaves the same
        event("floating", "DTSTART;TZID=Custom Zone:20241231T180000"),
    )

    def test_time_of_day(self):
        self.assertEqual(kept_uids(self.ics, f0="keep:DTSTART:time:09:00-09:59", d="remove"), ["utc"])
        self.assertEqual(kept_uids(self.ics, f0="keep:DTSTART:time:10:00-11:00", d="remove", tz="Europe/Berlin"), ["utc"])

    def test_time_wraps_midnight(self):
        self.assertEqual(kept_uids(self.ics, f0="keep:DTSTART:time:17:00-04:00", d="remove", tz="Europe/Berlin"), ["ny", "floating"])

    def test_weekday(self):
        self.assertEqual(kept_uids(self.ics, f0="keep:DTSTART:weekday:SA,SU", d="remove"), ["ny"])
        self.assertEqual(kept_uids(self.ics, f0="keep:DTSTART:weekday:SA,SU", d="remove", tz="Europe/Berlin"), ["ny"])
        self.assertEqual(kept_uids(self.ics, f0="keep:DTSTART:weekday:SU", d="remove", tz="Europe/Berlin"), ["ny"])
        self.assertEqual(kept_uids(self.ics, f0="keep:DTSTART:weekday:SU", d="remove"), [])

    def test_monthday_month_yearday(self):
        self.assertEqual(kept_uids(self.ics, f0="keep:DTSTART:monthday:1,29-31", d="remove"), ["utc", "allday", "floating"])
        self.assertEqual(kept_uids(self.ics, f0="keep:DTSTART:month:2-3", d="remove"), ["ny", "allday"])
        self.assertEqual(kept_uids(self.ics, f0="keep:DTSTART:yearday:60,366", d="remove"), ["allday", "floating"])

    def test_allday(self):
        self.assertEqual(kept_uids(self.ics, f0="remove:DTSTART:allday"), ["utc", "ny", "floating"])
        self.assertEqual(kept_uids(self.ics, f0="remove:DTSTART:allday:"), ["utc", "ny", "floating"])

    def test_dtend_and_missing_property(self):
        self.assertEqual(kept_uids(self.ics, f0="keep:DTEND:time:10:30-10:30", d="remove"), ["utc"])


class OrderingTest(unittest.TestCase):
    ics = calendar(
        event("exam-sat", "SUMMARY:Exam", "DTSTART:20240106T100000"),
        event("final-sat", "SUMMARY:Final Exam", "DTSTART:20240106T100000"),
        event("party-sat", "SUMMARY:Party", "DTSTART:20240106T200000"),
        event("exam-mon", "SUMMARY:Exam", "DTSTART:20240108T100000"),
    )

    def test_first_match_wins(self):
        args = {"f0": "keep:SUMMARY:contains:final", "f1": "remove:DTSTART:weekday:SA,SU"}
        self.assertEqual(kept_uids(self.ics, **args), ["final-sat", "exam-mon"])
        args = {"f0": "remove:DTSTART:weekday:SA,SU", "f1": "keep:SUMMARY:contains:final"}
        self.assertEqual(kept_uids(self.ics, **args), ["exam-mon"])

    def test_default_action(self):
        self.assertEqual(kept_uids(self.ics), ["exam-sat", "final-sat", "party-sat", "exam-mon"])
        self.assertEqual(kept_uids(self.ics, d="remove"), [])
        self.assertEqual(kept_uids(self.ics, f0="keep:SUMMARY:equals:exam", d="remove"), ["exam-sat", "exam-mon"])


class OutputTest(unittest.TestCase):
    def test_kept_parts_are_unchanged(self):
        timezone = "BEGIN:VTIMEZONE\r\nTZID:Europe/Berlin\r\nBEGIN:STANDARD\r\nTZOFFSETFROM:+0200\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"
        kept = event("1", "SUMMARY:A long\r\n  folded title", "BEGIN:VALARM", "ACTION:DISPLAY", "END:VALARM")
        removed = event("2", "SUMMARY:Remove me")
        ics = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + timezone + kept + removed + "END:VCALENDAR\r\n"
        expected = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + timezone + kept + "END:VCALENDAR\r\n"
        self.assertEqual(filter_calendar(ics, parse_filter_config({"f0": "remove:SUMMARY:equals:remove me"})), expected)

    def test_missing_trailing_newline(self):
        ics = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR"
        self.assertEqual(filter_calendar(ics, parse_filter_config({})), ics + "\r\n")


class InvalidConfigTest(unittest.TestCase):
    def assertInvalid(self, **args):
        with self.assertRaises(FilterError):
            parse_filter_config(args)

    def test_invalid_configs(self):
        self.assertInvalid(f0="drop:SUMMARY:contains:x")
        self.assertInvalid(f0="keep:SUMMARY")
        self.assertInvalid(f0="keep:SUMM ARY:contains:x")
        self.assertInvalid(f0="keep:SUMMARY:weekday:MO")
        self.assertInvalid(f0="keep:DTSTART:contains:x")
        self.assertInvalid(f0="keep:SUMMARY:regex:(unclosed")
        self.assertInvalid(f0="keep:SUMMARY:regex:" + "a" * 501)
        self.assertInvalid(f0="keep:SUMMARY:regex:(?V1)(unclosed")
        self.assertInvalid(f0="keep:DTSTART:time:25:00-26:00")
        self.assertInvalid(f0="keep:DTSTART:time:10:00")
        self.assertInvalid(f0="keep:DTSTART:weekday:XX")
        self.assertInvalid(f0="keep:DTSTART:monthday:0-5")
        self.assertInvalid(f0="keep:DTSTART:month:12-1")
        self.assertInvalid(f0="keep:DTSTART:yearday:")
        self.assertInvalid(tz="Not/AZone")
        self.assertInvalid(tz="../../etc/passwd")
        self.assertInvalid(d="maybe")
        self.assertInvalid(**{f"f{i}": "keep:SUMMARY:contains:x" for i in range(21)})

    def test_expensive_regexes(self):
        # these would allocate gigabytes when compiled
        self.assertInvalid(f0="keep:SUMMARY:regex:a{4294967294}")
        self.assertInvalid(f0="keep:SUMMARY:regex:(?:a{65535}){65535}")
        self.assertInvalid(f0="keep:SUMMARY:regex:(?:(?:a{100}){100}){100}")
        # verbose mode could hide repetition counts
        self.assertInvalid(f0="keep:SUMMARY:regex:(?x)a{1#\n000000}")
        self.assertInvalid(f0="keep:SUMMARY:regex:(?ix:a{ 1000000 })")

    def test_reasonable_regexes(self):
        for pattern in (r"\d{4}-\d{2}-\d{2}", "(?i)meeting|call", "Room [A-Z]{1,3}", "(?V1)x", "a{1,100000}"):
            parse_filter_config({"f0": "keep:SUMMARY:regex:" + pattern})

    def test_filters_after_a_gap_are_ignored(self):
        self.assertEqual(len(parse_filter_config({"f0": "keep:SUMMARY:contains:x", "f2": "garbage"}).filters), 1)


class LimitTest(unittest.TestCase):
    def test_too_many_event_lines(self):
        ics = calendar(event("1", *["SUMMARY:x"] * MAX_EVENT_LINES))
        with self.assertRaises(FilterError):
            filter_calendar(ics, parse_filter_config({}))

    def test_too_many_folded_lines(self):
        ics = calendar(event("1", "SUMMARY:x" + "\r\n x" * MAX_FOLDED_LINES))
        with self.assertRaises(FilterError):
            filter_calendar(ics, parse_filter_config({}))


if __name__ == '__main__':
    unittest.main()
