"""Fixtures modeled on the real page structures observed at each commission.

These are not inventions - each mirrors the markup shape of a live source:
  ICS_TRUMBA     -> calendar.in.gov (Indiana IURC)
  RSS_PUCT       -> puc.texas.gov/agency/calendar/getcalendarrss.aspx
  HTML_DRUPAL    -> dps.ny.gov/calendar, michigan.gov/mpsc
  HTML_JSONLD    -> modern .gov templates emitting schema.org/Event
  HTML_ASPX_TBL  -> psc.mo.gov/Calendars.aspx, floridapsc.com hearing schedule
  HTML_LOOSE     -> older PSC pages with dates in running text
"""

ICS_TRUMBA = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Trumba Corporation//Trumba Calendar//EN
BEGIN:VEVENT
UID:trumba-1@calendar.in.gov
DTSTAMP:20260810T120000Z
DTSTART;TZID=America/Indiana/Indianapolis:20260903T093000
DTEND;TZID=America/Indiana/Indianapolis:20260903T113000
SUMMARY:Evidentiary Hearing - Cause No. 46150 - Northern Indiana Public Service Company
LOCATION:PNC Center, 101 W Washington St, Indianapolis
DESCRIPTION:Petition of NIPSCO for authority to increase base rates and charges for electric utility service. Revenue requirement testimony.
URL:https://www.in.gov/iurc/cause/46150
END:VEVENT
BEGIN:VEVENT
UID:trumba-2@calendar.in.gov
DTSTAMP:20260810T120000Z
DTSTART;TZID=America/Indiana/Indianapolis:20260917T103000
SUMMARY:IURC Conference - Cause No. 46201 - Indiana Michigan Power Company
DESCRIPTION:Prehearing conference and preliminary hearing regarding I&M's integrated resource plan.
END:VEVENT
BEGIN:VEVENT
UID:trumba-3@calendar.in.gov
DTSTAMP:20260810T120000Z
DTSTART;VALUE=DATE:20261001
SUMMARY:CenterPoint Energy Indiana South - Testimony Due
DESCRIPTION:Cause No. 46080. Direct testimony deadline for OUCC.
END:VEVENT
END:VCALENDAR
"""

RSS_PUCT = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<title>PUCT Calendar</title>
<link>https://www.puc.texas.gov/agency/calendar/</link>
<item>
  <title>Open Meeting - September 10, 2026 9:30 AM</title>
  <link>https://www.puc.texas.gov/agency/calendar/agendas/</link>
  <description>Commission Open Meeting. Docket No. 56789 - Application of Oncor Electric Delivery Company LLC for Authority to Change Rates. Docket No. 57120 - CenterPoint Energy Houston Electric LLC system resiliency plan.</description>
  <pubDate>Mon, 01 Sep 2026 08:00:00 GMT</pubDate>
</item>
<item>
  <title>Public Hearing - September 24, 2026 10:00 AM</title>
  <link>https://www.puc.texas.gov/agency/calendar/</link>
  <description>Public comment hearing in Docket No. 57001, Application of Southwestern Electric Power Company for a rate increase.</description>
  <pubDate>Tue, 02 Sep 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>
"""

HTML_DRUPAL = """<!DOCTYPE html><html><body>
<div class="view-content">
  <div class="views-row">
    <h3><a href="/event/september-2026-psc-session">September 2026 Public Service Commission Session</a></h3>
    <time datetime="2026-09-17T10:30:00-04:00">Sep 17, 2026, 10:30 AM ET</time>
    <div class="field-location">Empire State Plaza, Albany, NY</div>
    <p>Case 24-E-0165 - Proceeding on Motion of the Commission as to the Rates of
       Consolidated Edison Company of New York, Inc. for Electric Service. Joint Proposal.</p>
  </div>
  <div class="views-row">
    <h3><a href="/event/or-public-statement-hearing">Orange and Rockland Public Statement Hearing</a></h3>
    <time datetime="2026-10-02T18:00:00-04:00">Oct 02, 2026, 6:00 PM ET</time>
    <div class="field-location">Virtual</div>
    <p>Case 25-G-0100 - Public comment hearing on the rate increase request of
       Orange and Rockland Utilities, Inc. for gas service.</p>
  </div>
  <div class="views-row">
    <h3><a href="/event/bliss-wind">Bliss Wind Repowering Project Public Comment Hearing</a></h3>
    <time datetime="2026-08-26T18:00:00-04:00">Aug 26, 2026, 6:00 PM ET</time>
    <div class="field-location">Rita George Recreation Hall, Bliss, NY</div>
  </div>
</div></body></html>"""

HTML_JSONLD = """<!DOCTYPE html><html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"Event","name":"Commission Voting Meeting",
  "startDate":"2026-09-03T11:00:00-07:00","endDate":"2026-09-03T14:00:00-07:00",
  "location":{"@type":"Place","name":"CPUC Auditorium, San Francisco"},
  "description":"Application A.25-05-011 of Pacific Gas and Electric Company for a general rate case. Also Southern California Edison cost of capital.",
  "url":"https://www.cpuc.ca.gov/voting/2026-09-03"},
 {"@type":"Event","name":"Evidentiary Hearing - SDG&E General Rate Case",
  "startDate":"2026-09-22T09:00:00-07:00",
  "description":"Application A.25-04-002, San Diego Gas and Electric Company revenue requirement.",
  "url":"https://www.cpuc.ca.gov/hearings/a2504002"}
]}
</script></head><body><p>Events</p></body></html>"""

HTML_ASPX_TBL = """<!DOCTYPE html><html><body>
<table class="rgMasterTable">
<tr><th>Date</th><th>Time</th><th>Type</th><th>Case</th><th>Description</th></tr>
<tr><td>09/15/2026</td><td>8:30 AM</td><td>Evidentiary Hearing</td><td>ER-2026-0087</td>
    <td><a href="/case/ER-2026-0087">In the Matter of Evergy Missouri West's Request for Authority to Increase Rates</a></td></tr>
<tr><td>10/07/2026</td><td>9:00 AM</td><td>Agenda</td><td>GR-2026-0110</td>
    <td><a href="/case/GR-2026-0110">Spire Missouri Inc. general rate increase - decision conference</a></td></tr>
<tr><td>10/21/2026</td><td>6:00 PM</td><td>Local Public Hearing</td><td>ER-2026-0087</td>
    <td>Local public hearing, St. Joseph MO</td></tr>
</table></body></html>"""

HTML_LOOSE = """<!DOCTYPE html><html><body><div id="content">
<p>The Commission will hold a public hearing on October 14, 2026 at 6:00 PM regarding
   the application of Appalachian Power Company for an increase in base rates, Case No. 26-0455-E-42T.</p>
<p>An evidentiary hearing in Case No. 26-0501-G-42T, Hope Gas, is scheduled for November 4, 2026.</p>
<p>Copyright 2026 Public Service Commission</p>
<li>December 2, 2026 - Wheeling Power Company ENEC annual review hearing</li>
</div></body></html>"""

HTML_WITH_ICS_LINK = """<!DOCTYPE html><html><head>
<link rel="alternate" type="text/calendar" href="/calendar/export.ics">
</head><body><p>Subscribe to our calendar</p></body></html>"""


# Federal Register API search response (mirrors the real shape returned for
# FERC Sunshine Act notices - the `dates` field carries the meeting datetime).
FR_API_JSON = b"""{
 "count": 2,
 "results": [
  {"title": "Sunshine Act Meetings",
   "publication_date": "2026-07-14",
   "dates": "September 17, 2026, 10:00 a.m.",
   "abstract": null,
   "html_url": "https://www.federalregister.gov/documents/2026/07/14/2026-14125/sunshine-act-meetings"},
  {"title": "Sunshine Act Meeting Notice",
   "publication_date": "2026-06-16",
   "dates": "June 18, 2026, 10:00 a.m.",
   "abstract": null,
   "html_url": "https://www.federalregister.gov/documents/2026/06/16/2026-12079/sunshine-act-meeting-notice"}
 ]
}"""


# Telerik RadScheduler init blob (PUCT). Appointment JSON is a string inside
# the JS config; rendered HTML has no per-event dates.
HTML_TELERIK = r"""<!DOCTYPE html><html><body>
<div class="RadScheduler"><div class="rsApt">grid shrapnel</div></div>
<script>
$create(Telerik.Web.UI.RadScheduler, {"_timeZoneId":"UTC","appointments":"[{\"id\":1,\"subject\":\"Open Meeting\",\"description\":\"\",\"start\":\"2026/08/14 09:30\",\"end\":\"2026/08/14 17:00\",\"resources\":[{\"attributes\":{\"Location\":\"Commissioners Hearing Room\",\"Cancelled\":\"False\",\"NavigateUrl\":\"https://ftp.puc.texas.gov/agenda.pdf\"}}]},{\"id\":2,\"subject\":\"Comments Due – Project No. 59432\",\"description\":\"\",\"start\":\"2026/09/04 16:30\",\"end\":\"2026/09/04 17:00\",\"resources\":[{\"attributes\":{\"Location\":\"NA\",\"Cancelled\":\"False\"}}]},{\"id\":3,\"subject\":\"Cancelled Meeting\",\"description\":\"\",\"start\":\"2026/08/21 09:30\",\"end\":\"2026/08/21 17:00\",\"resources\":[{\"attributes\":{\"Cancelled\":\"True\"}}]}]", "x":1});
</script></body></html>"""


# FullCalendar events feed (Maryland PSC admin-ajax). Datetimes ISO; a
# date-only start means all-day; "NO Administrative Meeting" is a notice.
FULLCALENDAR_JSON = b"""[
 {"id":1,"title":"Evidentiary Hearing: NGEA Transmission Storage Project Proposals (Case 9866)",
  "start":"2026-09-04T10:00:00","end":"2026-09-04T16:00:00","extendedProps":{}},
 {"id":2,"title":"Deadline for written comments","start":"2026-08-28"},
 {"id":3,"title":"NO Administrative Meeting","start":"2026-08-19T00:00:00"},
 {"id":4,"title":"Ancient meeting","start":"2020-01-01T10:00:00"}
]"""


# Indiana IURC weekly hearing list: labelled blocks (date / CAUSE NO. / TIME /
# ROOM / caption). Line-by-line scanning shreds this; block parsing recovers it.
PDF_BLOCK_TEXT = """HEARINGS BEFORE THE
INDIANA UTILITY REGULATORY COMMISSION
FOR THE WEEK BEGINNING
MONDAY, AUGUST 17, 2026
Monday, August 17, 2026
CAUSE NO.: 37389-GCA147 ALJ: WILLIAMS
TIME: 9:30 A.M.
ROOM: PNC, Room 222
APPLICATION OF WESTFIELD GAS, LLC D/B/A CITIZENS GAS OF
WESTFIELD FOR A CHANGE IN ITS GAS COST ADJUSTMENT CHARGE.
Monday, August 17, 2026
CAUSE NO.: 46150 ALJ: SMITH
TIME: 1:00 P.M.
ROOM: PNC, Room 222
VERIFIED PETITION OF INDIANA MICHIGAN POWER COMPANY (I&M)
FOR ISSUANCE OF A CERTIFICATE.
"""

# Montana publishes only a PDF; its meetings are declared in prose lines.
PDF_LINE_TEXT = """MONTANA PUBLIC SERVICE COMMISSION
Commission Meetings:
August 17, 2026 - August 21, 2026
Date Published: August 13, 2026
Commission Scheduling Meeting: August 18, 2026, 1:00 PM
Commission Business Meeting: August 18, 2026, 1:30 PM
1. Approval of the Commission Business Meeting Minutes for the week of
August 10, 2026.
"""
