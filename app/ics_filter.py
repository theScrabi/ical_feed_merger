#!/usr/bin/python3

# ICS Feed Filter
#
# @author Christian Schabesberger
#
# Copyright (C) Christian Schabesberger 2024


from datetime import date, datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from functools import lru_cache
from typing import Dict, Iterator, List, Optional, Set, Tuple, Union, Final
import time
import regex


MAX_FILTERS:Final = 20
MAX_REGEX_LENGTH:Final = 500
# The regex module unrolls counted repetitions like a{1000} when compiling,
# so a short pattern can allocate gigabytes. This bounds
# len(pattern) * product of all repetition counts, roughly 100 bytes each.
MAX_REGEX_COST:Final = 200_000
# A legit event has at most a few hundred content lines.
MAX_EVENT_LINES:Final = 10_000
MAX_FOLDED_LINES:Final = 10_000
# Total time budget for filtering one calendar.
# Together with FETCH_DEADLINE it must stay well below gunicorn's worker timeout.
FILTER_DEADLINE:Final = 5

ACTIONS:Final = ("keep", "remove")
TEXT_OPS:Final = ("contains", "equals", "regex")
DATE_PROPERTIES:Final = ("DTSTART", "DTEND")
DATE_OPS:Final = ("time", "weekday", "monthday", "month", "yearday", "allday")
WEEKDAYS:Final = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
# text properties that hold a comma separated list of values
LIST_PROPERTIES:Final = ("CATEGORIES", "RESOURCES")

PROPERTY_NAME_PATTERN:Final = regex.compile(r'[A-Z][A-Z0-9-]*')
TIME_RANGE_PATTERN:Final = regex.compile(r'(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})')
NUMBER_RANGE_PATTERN:Final = regex.compile(r'(\d+)(?:-(\d+))?')
REPETITION_PATTERN:Final = regex.compile(r'\{(\d+)')
# In verbose mode whitespace and comments may appear inside {}, which would
# hide repetition counts from REPETITION_PATTERN.
VERBOSE_FLAG_PATTERN:Final = regex.compile(r'\(\?[a-zA-Z0-9^-]*x')


class FilterError(Exception):
    pass


# --- iCal parsing ---
# The calendar is not re-serialised. Every content line keeps the physical
# lines it was read from, so kept events are written out byte for byte.

def split_unquoted(s:str, separator:str) -> List[str]:
    parts:List[str] = []
    in_quotes = False
    start = 0
    for i, c in enumerate(s):
        if c == '"':
            in_quotes = not in_quotes
        elif c == separator and not in_quotes:
            parts.append(s[start:i])
            start = i + 1
    parts.append(s[start:])
    return parts

def parse_content_line(line:str) -> Tuple[str, Dict[str, str], str]:
    head_and_value = split_unquoted(line, ':')
    if len(head_and_value) < 2:
        return line.upper(), {}, ''
    head = head_and_value[0]
    value = line[len(head) + 1:]
    name, *raw_params = split_unquoted(head, ';')
    params:Dict[str, str] = {}
    for raw_param in raw_params:
        key, _, param_value = raw_param.partition('=')
        params[key.upper()] = param_value.strip('"')
    return name.upper(), params, value


class ContentLine:
    def __init__(self, raw_lines:List[str]):
        self.raw_lines = raw_lines
        unfolded = raw_lines[0] + ''.join(l[1:] for l in raw_lines[1:])
        self.name, self.params, self.value = parse_content_line(unfolded)


# Lines are produced lazily so a feed made of millions of tiny lines
# doesn't have to be held in memory as objects all at once.
def iter_physical_lines(ics:str) -> Iterator[str]:
    start = 0
    trailing_empty_lines = 0
    while start < len(ics):
        end = ics.find('\n', start)
        if end == -1:
            line = ics[start:]
            start = len(ics)
        else:
            line = ics[start:end - 1] if end > start and ics[end - 1] == '\r' else ics[start:end]
            start = end + 1
        # empty lines at the end of the calendar are dropped
        if line == '':
            trailing_empty_lines += 1
            continue
        for _ in range(trailing_empty_lines):
            yield ''
        trailing_empty_lines = 0
        yield line

def iter_content_lines(ics:str) -> Iterator[ContentLine]:
    group:Optional[List[str]] = None
    for line in iter_physical_lines(ics):
        if line[:1] in (' ', '\t') and group is not None:
            if len(group) >= MAX_FOLDED_LINES:
                raise FilterError(f"Line is too long, at most {MAX_FOLDED_LINES} folded lines are supported.")
            group.append(line)
        else:
            if group is not None:
                yield ContentLine(group)
            group = [line]
    if group is not None:
        yield ContentLine(group)

def read_content_lines(ics:str) -> List[ContentLine]:
    return list(iter_content_lines(ics))


def unescape_text(value:str, split_list:bool) -> List[str]:
    items:List[str] = []
    current:List[str] = []
    escaped = False
    for c in value:
        if escaped:
            current.append('\n' if c in 'nN' else c)
            escaped = False
        elif c == '\\':
            escaped = True
        elif c == ',' and split_list:
            items.append(''.join(current))
            current = []
        else:
            current.append(c)
    items.append(''.join(current))
    return items


@lru_cache(maxsize=64)
def load_zone(name:str) -> Optional[ZoneInfo]:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None

def parse_date_value(line:ContentLine, tz:Optional[tzinfo]) -> Optional[Union[date, datetime]]:
    value = line.value.strip()
    try:
        if line.params.get('VALUE', '').upper() == 'DATE' or len(value) == 8:
            return datetime.strptime(value[:8], '%Y%m%d').date()
        dt = datetime.strptime(value[:15], '%Y%m%dT%H%M%S')
    except ValueError:
        return None
    if value.upper().endswith('Z'):
        dt = dt.replace(tzinfo=timezone.utc)
    elif 'TZID' in line.params:
        zone = load_zone(line.params['TZID'])
        if zone is not None:
            dt = dt.replace(tzinfo=zone)
    if tz is not None and dt.tzinfo is not None:
        dt = dt.astimezone(tz)
    return dt


class Event:
    def __init__(self, lines:List[ContentLine], properties:List[ContentLine]):
        # lines: everything including nested components like VALARM
        # properties: only the event's own properties
        self.lines = lines
        self.properties = properties

    def get_all(self, name:str) -> List[ContentLine]:
        return [p for p in self.properties if p.name == name]


# --- filters ---

def remaining_time(deadline:float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FilterError("Filtering took too long.")
    return remaining


class Filter:
    def __init__(self, keep:bool, property_name:str):
        self.keep = keep
        self.property_name = property_name

    def matches(self, event:Event, deadline:float) -> bool:
        raise NotImplementedError()


def regex_cost(pattern:str) -> int:
    # Upper bound, as if all repetitions were nested in each other.
    cost = len(pattern)
    for m in REPETITION_PATTERN.finditer(pattern):
        cost *= max(int(m.group(1)), 1)
        if cost > MAX_REGEX_COST:
            break
    return cost


class TextFilter(Filter):
    def __init__(self, keep:bool, property_name:str, op:str, value:str):
        super().__init__(keep, property_name)
        self.op = op
        self.needle = value.casefold()
        self.pattern = None
        if op == "regex":
            if len(value) > MAX_REGEX_LENGTH:
                raise FilterError(f"Regex is too long, at most {MAX_REGEX_LENGTH} characters are supported.")
            if VERBOSE_FLAG_PATTERN.search(value) is not None:
                raise FilterError(f"Invalid regex {value}: the verbose flag (?x) is not supported.")
            if regex_cost(value) > MAX_REGEX_COST:
                raise FilterError(f"Regex {value} is too complex, use smaller {{n}} repetition counts.")
            try:
                # don't keep user supplied patterns in the module wide cache
                self.pattern = regex.compile(value, cache_pattern=False)
            except Exception as e:
                # the regex module raises more than regex.error for some invalid patterns
                raise FilterError(f"Invalid regex {value}: {e}")

    def matches(self, event:Event, deadline:float) -> bool:
        split_list = self.property_name in LIST_PROPERTIES
        texts = [text for line in event.get_all(self.property_name)
                 for text in unescape_text(line.value, split_list)]
        if len(texts) == 0:
            texts = ['']
        for text in texts:
            if self.op == "contains" and self.needle in text.casefold():
                return True
            if self.op == "equals" and self.needle == text.casefold():
                return True
            if self.op == "regex":
                try:
                    if self.pattern.search(text, timeout=remaining_time(deadline)):
                        return True
                except TimeoutError:
                    raise FilterError("Filtering took too long.")
        return False


def parse_number_set(value:str, low:int, high:int, what:str) -> Set[int]:
    numbers:Set[int] = set()
    for part in value.split(','):
        m = NUMBER_RANGE_PATTERN.fullmatch(part.strip())
        if m is None:
            raise FilterError(f"Invalid {what} list {value}.")
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) is not None else start
        if not (low <= start <= end <= high):
            raise FilterError(f"Invalid {what} list {value}, values must be between {low} and {high}.")
        numbers.update(range(start, end + 1))
    return numbers

def parse_time_range(value:str) -> Tuple[int, int]:
    m = TIME_RANGE_PATTERN.fullmatch(value.strip())
    if m is None:
        raise FilterError(f"Invalid time range {value}, expected HH:MM-HH:MM.")
    from_hour, from_minute, to_hour, to_minute = (int(g) for g in m.groups())
    if from_hour > 23 or to_hour > 23 or from_minute > 59 or to_minute > 59:
        raise FilterError(f"Invalid time range {value}.")
    return from_hour * 60 + from_minute, to_hour * 60 + to_minute

def parse_weekdays(value:str) -> Set[int]:
    weekdays:Set[int] = set()
    for part in value.split(','):
        day = part.strip().upper()
        if day not in WEEKDAYS:
            raise FilterError(f"Invalid weekday list {value}, expected e.g. MO,TU,SA.")
        weekdays.add(WEEKDAYS.index(day))
    return weekdays


class DateFilter(Filter):
    def __init__(self, keep:bool, property_name:str, op:str, value:str, tz:Optional[tzinfo]):
        super().__init__(keep, property_name)
        self.op = op
        self.tz = tz
        if op == "time":
            self.time_range = parse_time_range(value)
        elif op == "weekday":
            self.numbers = parse_weekdays(value)
        elif op == "monthday":
            self.numbers = parse_number_set(value, 1, 31, "day of month")
        elif op == "month":
            self.numbers = parse_number_set(value, 1, 12, "month")
        elif op == "yearday":
            self.numbers = parse_number_set(value, 1, 366, "day of year")

    def matches(self, event:Event, deadline:float) -> bool:
        lines = event.get_all(self.property_name)
        if len(lines) == 0:
            return False
        d = parse_date_value(lines[0], self.tz)
        if d is None:
            return False
        is_all_day = not isinstance(d, datetime)
        if self.op == "allday":
            return is_all_day
        if self.op == "time":
            if is_all_day:
                return False
            minute = d.hour * 60 + d.minute
            start, end = self.time_range
            if start <= end:
                return start <= minute <= end
            return minute >= start or minute <= end
        if self.op == "weekday":
            return d.weekday() in self.numbers
        if self.op == "monthday":
            return d.day in self.numbers
        if self.op == "month":
            return d.month in self.numbers
        if self.op == "yearday":
            return d.timetuple().tm_yday in self.numbers
        return False


class FilterConfig:
    def __init__(self, filters:List[Filter], default_keep:bool):
        self.filters = filters
        self.default_keep = default_keep

    def keeps(self, event:Event, deadline:float) -> bool:
        # the first matching filter decides
        for f in self.filters:
            if f.matches(event, deadline):
                return f.keep
        return self.default_keep


def parse_filter(raw_filter:str, tz:Optional[tzinfo]) -> Filter:
    parts = raw_filter.split(':', 3)
    if len(parts) < 3:
        raise FilterError(f"Invalid filter {raw_filter}, expected action:property:operation:value.")
    action, property_name, op = parts[0].lower(), parts[1].upper(), parts[2].lower()
    value = parts[3] if len(parts) == 4 else ''
    if action not in ACTIONS:
        raise FilterError(f"Invalid filter action {action}, expected one of {', '.join(ACTIONS)}.")
    keep = action == "keep"
    if PROPERTY_NAME_PATTERN.fullmatch(property_name) is None:
        raise FilterError(f"Invalid property {property_name}.")
    if property_name in DATE_PROPERTIES:
        if op not in DATE_OPS:
            raise FilterError(f"Invalid operation {op} for {property_name}, expected one of {', '.join(DATE_OPS)}.")
        return DateFilter(keep, property_name, op, value, tz)
    if op not in TEXT_OPS:
        raise FilterError(f"Invalid operation {op} for {property_name}, expected one of {', '.join(TEXT_OPS)}.")
    return TextFilter(keep, property_name, op, value)

def parse_filter_config(args) -> FilterConfig:
    tz = None
    tz_name = args.get('tz')
    if tz_name:
        tz = load_zone(tz_name)
        if tz is None:
            raise FilterError(f"Unknown timezone {tz_name}.")

    default_action = args.get('d', 'keep').lower()
    if default_action not in ACTIONS:
        raise FilterError(f"Invalid default action {default_action}, expected one of {', '.join(ACTIONS)}.")

    i = 0
    filters:List[Filter] = []
    while True:
        raw_filter = args.get(f'f{i}')
        if raw_filter is None:
            break
        if i >= MAX_FILTERS:
            raise FilterError(f"Too many filters, at most {MAX_FILTERS} are supported.")
        filters.append(parse_filter(raw_filter, tz))
        i += 1
    return FilterConfig(filters, default_action == "keep")


def filter_calendar(ics:str, config:FilterConfig) -> str:
    deadline = time.monotonic() + FILTER_DEADLINE
    output:List[str] = []
    event:Optional[Event] = None
    depth = 0
    for line in iter_content_lines(ics):
        remaining_time(deadline)
        is_begin = line.name == 'BEGIN'
        is_end = line.name == 'END'
        if event is None:
            if is_begin and line.value.strip().upper() == 'VEVENT':
                event = Event([line], [])
                depth = 1
            else:
                output.extend(line.raw_lines)
            continue

        event.lines.append(line)
        if len(event.lines) > MAX_EVENT_LINES:
            raise FilterError(f"Event is too large, at most {MAX_EVENT_LINES} lines per event are supported.")
        if is_begin:
            depth += 1
        elif is_end:
            depth -= 1
        elif depth == 1:
            event.properties.append(line)

        if depth == 0:
            if config.keeps(event, deadline):
                for event_line in event.lines:
                    output.extend(event_line.raw_lines)
            event = None

    if event is not None:
        # unterminated event, pass it through untouched
        for event_line in event.lines:
            output.extend(event_line.raw_lines)
    return '\r\n'.join(output) + '\r\n'
