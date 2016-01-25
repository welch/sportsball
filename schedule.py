#
# giants schedule manager. we cache a parsed, processed version
# of their ical schedule feed as a DataStore object.
#
import logging
import json
from datetime import datetime, timedelta, date
from pytz import timezone, utc
from google.appengine.ext import db
from ics import Calendar
from urllib2 import urlopen

# This iCal URL will be updated throughout the season as schedules change
SCHED_URL = 'http://mlb.am/tix/giants_schedule_full'

# for sanity, all date/time storage and manipulations will be in AT&T
# Park's local TZ
ATT_TZ = timezone('US/Pacific')

def localize(dt):
    """
    set tzinfo for dt to ATT&T Park timezone, converting if it already
    has tzinfo set.
    """
    if dt.tzinfo == None:
        return ATT_TZ.localize(dt)
    else:
        return dt.astimezone(ATT_TZ)

def attnow():
    """now in the ATT_TZ"""

    return localize(datetime.now(utc))

class Schedule(db.Model):
    """
    Factory for schedule instances backed by entities in the datastore.
    Don't instantiate this class -- use Schedule.get() to retrieve an
    instance by url.

    """
    url =  db.StringProperty() # feed url, also used as primary key
    json = db.TextProperty()
    timestamp = db.DateTimeProperty(auto_now=True)
    _events = {} # event lists cached by isodate

    @classmethod
    def get(cls, url=SCHED_URL, every_secs=(24 * 3600)):
        """
        fetch the cached schedule for this url from the datastore. If it
        does not exist, or is over every_secs seconds old, refresh it from the
        url feed before returning.

        Returns: Schedule instance for the url

        """
        sched = cls.all().filter("url ==", url).get()
        if (not sched or not sched.json or
            sched.timestamp < datetime.now() - timedelta(seconds=every_secs)):
            sched = cls.refresh(url=url)
        if not sched:
            logging.error("cannot fetch sched.json from DataStore")
            return None
        return sched

    @classmethod
    def refresh(cls, url=SCHED_URL):
        """
        update our schedule.json from the feed url and store it in
        DataStore, creating a persistent Schedule object if necessary.

        Returns: Schedule instance with refreshed schedule json.

        """
        sched = cls.all().filter("url ==", url).get()
        if not sched:
            sched = cls()
            sched.url = url
            sched.json = None
            sched._events = {} # cache for parsed events, not persisted.
        events = get_feed(url)
        if events:
            sched.json = json.dumps(events)
            sched._events = {}
        sched.put()
        return sched

    def get_events(self, min_isodate=None):
        """decode instance json into a dictionary of event dictionaries keyed
        by isodate string, limited by min_isodate (today if null).
        Cache the parsed dictionary for speed during the lifetime of
        this app.

        Returns: list of event dictionaries for given date and beyond

        """
        if not min_isodate:
            min_isodate = attnow().date().isoformat()
        try:
            return self._events[min_isodate]
        except KeyError:
            pass
        schedule = [e for e in json.loads(self.json) if min_isodate <= e['date']]
        self._events[min_isodate] = schedule
        return schedule

    def get_next_here_event(self, isodate=None):
        """
        return an event dictionary for the earliest event on or after
        the given date, or None if no events are scheduled.
        If isodate is None, use today

        Returns: an event dictionary, or none if no more games scheduled for here
        """
        for e in self.get_events(isodate):
            if e['is_here']:
                return e
        return None

    @staticmethod
    def next_isodate(iso, days=1):
        """
        add days to isodate, return new isodate string
        """
        next = (datetime.strptime(iso, "%Y-%m-%d").date() + timedelta(days=days))
        return next.isoformat()

    def get_next_non_here_datetime(self, isodate=None):
        """
        return the earliest day on or after the given date having no
        event here. If isodate is None, use today.

        Returns: an isodate string
        """
        if isodate is None:
            isodate = attnow().date().isoformat()
        for e in self.get_events(isodate):
            if (not e['is_here'] or isodate != e['date']):
                break
            isodate = Schedule.next_isodate(isodate)
        return datetime.strptime(isodate, "%Y-%m-%d")

def get_feed(url=SCHED_URL):
    """
    fetch the giants schedule as a remote csv file, parse it,
    and create a list of events from it.

    Returns: a sorted list of event dictionaries, or None
    if there is a problem (eg, an http timeout. they happen.)
    """
    sched = []
    logging.info("get_feed %s" % url)
    try:
        c = Calendar(urlopen(SCHED_URL).read().decode('iso-8859-1'))
        for event in c.events:
            if event.name.startswith("FINAL"):
                continue # skip games already played
            event.begin = localize(event.begin)
            is_home = (event.name.endswith("Giants"))
            is_here = event.location.startswith('AT&T')
            them = event.name.split(" vs. ")[0 if is_home else 1]
            sched.append({
                'date': event.begin.date().isoformat(),
                'day': event.begin.strftime("%A, %b %d"),
                'time': event.begin.strftime("%I:%M %p"),
                'is_home': is_home,
                'is_here': is_here,
                'location': event.location,
                'them': them
            })
    except Exception, e:
        logging.error("can't download/parse schedule: " + str(e))
        return None
    return sched
