from schedule import Schedule, attnow
from google.appengine.ext.webapp import template
import webapp2
import logging
from datetime import date, datetime

def sched_message(isodate=None):
    """
    return an informative message about today's event,
    as a list of strings (one per line)
    """
    if not isodate:
        isodate = attnow().date().isoformat()
    sched = Schedule.get()
    e = sched.get_next_here_event(isodate)
    if not e:
        return ['No more home games!', "(...until next year...)"]
    elif e['date'] != isodate:
        return ['No home game today!',
                'All quiet until %s, when Giants play %s at %s' % (
                    e['day'], e['them'], e['time'])]
    else:
        quiet = sched.get_next_non_here_datetime(isodate)
        return ['Giants play %s at %s\n' % (e['them'], e['time']),
                "No peace and quiet until %s" % quiet.strftime("%A, %b %d")]

class IndexPage(webapp2.RequestHandler):
    def get(self, isodate):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("\n".join(sched_message(isodate)))

class EightballPage(webapp2.RequestHandler):
    def get(self, verb="hosed", isodate=None):
        if not isodate:
            isodate = attnow().date().isoformat()
        sched = Schedule.get()
        e = sched.get_next_here_event(isodate)
        logging.info("for isodate %s, isodatetime %s, next home event is %s" % (
                isodate, attnow(), str(e)))
        is_home = e and e['date'] == isodate
        message = sched_message(isodate)
        self.response.write(
            template.render('templates/8ball.w2', {
                    'verb': verb,
                    'is_home': is_home,
                    'today': message[0],
                    'tomorrow': message[1]
                    }))

class SchedulePage(webapp2.RequestHandler):
    def get(self):
        sched = Schedule.get()
        self.response.headers['Content-Type'] = 'application/json'
        self.response.write(sched.json)

class RefreshPage(webapp2.RequestHandler):
    def get(self):
        sched = Schedule.get(every_secs = 10) # a little DOS protection here
        self.response.headers['Content-Type'] = 'application/json'
        self.response.write("Refreshed schedule\n")

app = webapp2.WSGIApplication([
    webapp2.Route('/schedule.json', handler=SchedulePage),
    webapp2.Route('/refresh', handler=RefreshPage),
    webapp2.Route('/<verb>/<isodate>', handler=EightballPage),
    webapp2.Route('/<verb>/', handler=EightballPage),
    webapp2.Route('/<isodate>', handler=EightballPage),
    webapp2.Route('/', handler=EightballPage)
])
