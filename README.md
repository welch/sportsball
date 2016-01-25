# _Sportsball_

### (aka "Is My Day @#$%#'d?")

_Sportsball_ is a Google App Engine application for my sportsball
schedule-reporting websites like [giant8ball.com](http://giant8ball.com).
These web pages speak to the central question of life in San Francisco's
South Beach neighborhood -- is my day going to be hosed by a Giants home game
and those who love them?

Giants schedule url (iCal):
http://mlb.am/tix/giants_schedule_full

### Local installation and testing

If you want to deploy your own version of _Sportsball_, you'll need to
create a [Google App Engine account](https://developers.google.com/appengine/). If you'd just like to try it out on
your local machine, [download the Python SDK](https://developers.google.com/appengine/downloads#Google_App_Engine_SDK_for_Python) and install it. If you
are working in OS X, you'll be installing a Launcher OS X application,
but this will also install symlinks for python commandline scripts for
the development server and uploader in /usr/local/google_appengine/.

You can test this locally by running (from this directory):

```
> dev_appserver.py .
```

Connect to http://localhost:8080, and you should be presented with a
giant 8-ball answering today's schedule question. It's not pretty --
this project was to get a little experience with a long-lived
application on GAE, not presentation. Contributions improving the user
experience are most welcome.

### Uploading to GAE

To upload this application to your Google App Engine hosting
environment, you'll first need to apply for a unique application
id/name for your deployed app via your console at https://appengine.google.com/ .
Change the first line of the `app.yaml` file in this directory to

```
application: your-app-name
```

Then, from this directory run:

```
> appcfg.py --oauth2 update .
```

The first time you do this, you'll be routed through a web page that
lets you autheticate to the appengine service, thereafter the credentials
are cached (see https://developers.google.com/appengine/docs/python/tools/uploadinganapp#Python_Password-less_login_with_OAuth2 for more).

If all was successfull, your app is now up and running in the cloud.
