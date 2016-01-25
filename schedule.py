#
# giants schedule manager. we cache a parsed, processed version
# of their csv schedule feed as a DataStore object.
#
import urllib
import csv
import logging
import json
from datetime import datetime, timedelta, date
from pytz import timezone, utc
from google.appengine.ext import db

SCHED_URL = 'http://mlb.mlb.com/soa/ical/schedule.csv?team_id=137&season=2015'

# for sanity, all date/time storage and manipulations will be in AT&T
# Park's local TZ
ATT_TZ = timezone('US/Pacific')

def localize(dt):
    """
    set tzinfo for dt to the local timezone, converting if it already
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
    save a json-encoded schedule as a long string in the DataStore.
    we'll serve this to javascript clients wanting to display event
    information.
    """
    url =  db.StringProperty() # feed url, also used as primary key
    json = db.TextProperty()
    timestamp = db.DateTimeProperty(auto_now=True)

    _events = {} # event lists cached by url+isodate

    @classmethod
    def get(cls, url=SCHED_URL, max_age=timedelta(days=1)):
        """
        fetch the currently cached schedule from the datastore. If it does
        not exist, or is over max_age old, update it from the url feed before
        returning.

        Returns: Schedule object for the current schedule
        """
        sched = cls.all().filter("url ==", url).get()
        if (not sched or not sched.json or
            sched.timestamp < datetime.now() - max_age):
            sched = cls.refresh(url=url)
        if not sched:
            logging.error("cannot fetch sched.json from DataStore")
            return None
        return sched

    @classmethod
    def refresh(cls, url=SCHED_URL):
        """
        update our cached, parsed schedule.json from the feed url and
        store it in DataStore, creating the Schedule object if
        necessary.

        Returns: fresh Schedule object for the current schedule
        """
        sched = cls.all().filter("url ==", url).get()
        if not sched:
            sched = cls()
            sched.url = url
            sched.json = None
        events = cls.get_feed(url)
        if events:
            sched.json = json.dumps(events)
        sched.put()
        return sched

    @staticmethod
    def get_feed(url=SCHED_URL):
        """
        fetch the giants schedule as a remote csv file, parse it,
        compute is_home, is_here, and them fields for each event.

        Returns: a sorted list of event dictionaries, or None
        if there is a problem (eg, an http timeout. they happen.)
        """
        sched = []
        logging.info("get_feed %s" % url)
        try:
            sched_csv = csv.DictReader(urllib.urlopen(url))
            sched_csv.fieldnames = [f.lower() for f in sched_csv.fieldnames]
            for row in sched_csv:
                # Giants csv schedule is given as Pacific timezone
                date = datetime.strptime(row['start date'], "%m/%d/%y").date()
                is_home = (row['subject'].endswith("Giants"))
                them = row['subject'].split(" at ")[0 if is_home else 1]
                sched.append({
                    'date': date.isoformat(),
                    'time': row['start time'],
                    'is_home': is_home,
                    'is_here': (row['location'] == 'AT&T Park'),
                    'location': row['location'],
                    'them': them
                    })
        except Exception, e:
            logging.error("can't download/parse schedule: " + str(e))
            return None
        return sched

    def get_events(self, min_isodate=None):
        """
        decode our json into a dictionary of event dictionaries keyed
        by isodate string, limited by min_isodate, which is treated as
        today if null.  cache the parsed dictionary so it is fast
        for others during the lifetime of this app.

        Returns: list of event dictionaries.
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

    def get_next_home_event(self, isodate=None):
        """
        return an event dictionary for the earliest event on or after
        the given date, or None if no events are scheduled.
        If isodate is None, use today

        Returns: an event dictionary, or none if no more home games scheduled
        """
        schedule = self.get_events(isodate)
        for i in xrange(len(schedule)):
            if schedule[i]['is_home']:
                return schedule[i]
        return None

    @staticmethod
    def next_isodate(iso, days=1):
        """
        add days to isodate, return new isodate string
        """
        next = (datetime.strptime(iso, "%Y-%m-%d").date() + timedelta(days=days))
        return next.isoformat()

    @staticmethod
    def day_of_isodate(iso):
        """
        return day name for the date
        """
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%A")

    def get_next_non_home_day(self, isodate=None):
        """
        return the earliest day on or after the given date having no
        home event. If isodate is None, use today.

        Returns: an isodate string
        """
        if isodate is None:
            isodate = Schedule.today()
        schedule = self.get_events(isodate)
        for i in xrange(len(schedule)):
            if (not schedule[i]['is_home'] or isodate != schedule[i]['date']):
                break
            isodate = Schedule.next_isodate(isodate)
        return isodate
