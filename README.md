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
create a
[Google App Engine account](https://developers.google.com/appengine/).
And get the [GCloud SDK](https://cloud.google.com/sdk/docs/) installed
and yourself authenticated.  Once you are all set up, you can test
this locally by running (from this directory):

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
id/name for your deployed app via your console at
`https://appengine.google.com/`.

Run the following to deploy or update your app. Increment the `--version`
argument when code changes.

```
gcloud app deploy app.yaml --project YOUR-PROJECT-ID --version YOUR-VERSION
```

Gcloud will describe the service it is about to deploy (yours!)
(and of will probably suggest that you update components, because gcloud).
Say yes and watch the deployment magic proceed.
By default, 100% of traffic will be routed to this new version.

If all was successfull, your app is now up and running in the cloud.
