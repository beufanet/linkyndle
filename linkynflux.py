#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import datetime
import locale
from dateutil.relativedelta import relativedelta
from influxdb import InfluxDBClient
import linky
import json

import argparse
import logging
import pprint

PFILE = "/.params"

# Sub to return format wanted by linky.py
def _dayToStr(date):
    return date.strftime("%d/%m/%Y")

# Open file with params for influxdb, enedis API and HC/HP time window
def _openParams(pfile):
    try:
        p = os.getcwd()+pfile
        f = open(p, 'r')
        try:
            array = json.load(f)
        except ValueError as e:
            logging.error('decoding JSON has failed', e)
            sys.exit(1)
    except IOError:
        logging.error('cannot open %s', p)
        sys.exit(1)
    else:
        f.close()
        return array

# Sub to get StartDate depending today - daysNumber
def _getStartDate(today, daysNumber):
    return _dayToStr(today - relativedelta(days=daysNumber))

# Get the midnight timestamp fro startDate
def _getStartTS(daysNumber):
    date = (datetime.datetime.now().replace(hour=0,minute=0,second=0,microsecond=0) - relativedelta(days=daysNumber))
    return date.timestamp()

# Get the timestamp for calculating if we are in HP / HC
def _getDateTS(y,mo,d,h,m):
    date = (datetime.datetime(year=y,month=mo,day=d,hour=h,minute=m,second=1,microsecond=0))
    return date.timestamp()


# Let's start here !

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("-d",  "--days",    type=int, help="Number of days from now to download", default=1)
    parser.add_argument("-v",  "--verbose", action="store_true", help="More verbose", default=False)
    args = parser.parse_args()

    pp = pprint.PrettyPrinter(indent=4)
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)

    params = _openParams(PFILE)

    # Try to log in InfluxDB Server
    try:
        logging.info("logging in InfluxDB Server Host %s...", params['influx']['host'])
        client = InfluxDBClient(params['influx']['host'], params['influx']['port'],
                    params['influx']['username'], params['influx']['password'],
                    params['influx']['db'], ssl=params['influx']['ssl'], verify_ssl=params['influx']['verify_ssl'])
        logging.info("logged in InfluxDB Server Host %s succesfully", params['influx']['host'])
    except:
        logging.error("unable to login on %s", params['influx']['host'])
        sys.exit(1)

    # Try to log in Enedis API
    try:
        logging.info("logging in Enedis URI %s...", linky.API_BASE_URI)
        token = linky.login(params['enedis']['username'], params['enedis']['password'])
        logging.info("logged in successfully!")
    except linky.LinkyLoginException as exc:
        logging.error("unable to login on %s : %s", linky.API_BASE_URI, e)
        sys.exit(1)

    # Calculate start/endDate and firstTS for data to request/parse
    startDate = _getStartDate(datetime.date.today(), args.days)
    endDate = _dayToStr(datetime.date.today())
    firstTS =  _getStartTS(args.days)

    # Try to get data from Enedis API
    try:
        logging.info("get Data from Enedis from {0} to {1}".format(startDate, endDate))
        # Get result from Enedis by 30m
        resEnedis = linky.get_data_per_hour(token, startDate, endDate)
        if (args.verbose):
            pp.pprint(resEnedis)
    except:
        logging.error("unable to get data from enedis")
        sys.exit(1)

    # When we have all values let's start parse data and pushing it
    jsonInflux = []
    for d in resEnedis['graphe']['data']:
        # Use the formula to create timestamp, 1 ordre = 30min
            tres = firstTS + ((d['ordre'] - 1) *30*60)
            t = datetime.datetime.fromtimestamp(tres)
            creuses = 0
            for hc in params['hc']:
                startTS = _getDateTS(t.year,t.month,t.day,hc['start']['h'],hc['start']['m'])
                endTS =   _getDateTS(t.year,t.month,t.day,hc['end']['h'],hc['end']['m'])
                if (startTS <= tres) and (endTS >= tres):
                    logging.debug("Found HC, set flag for DT : ", t.strftime('%Y-%m-%dT%H:%M:%SZ'))
                    creuses = 1
            # Warning if ordre = 30min, then kWh should be divided by 2 !
            logging.info(("found value ordre({0:3d}) : {1:7.2f} kWh at {2} (HC:{3})").format(d['ordre'], (d['valeur']/2), t.strftime('%Y-%m-%dT%H:%M:%SZ'),creuses))
            jsonInflux.append({
                           "measurement": "conso_elec",
                           "tags": {
                               "fetch_date" : endDate,
                               "heures_creuses" : creuses,
                           },
                           "time": t.strftime('%Y-%m-%dT%H:%M:%SZ'),
                           "fields": {
                               "value": (d['valeur']*1000)/2,
                               "max": resEnedis['graphe']['puissanceSouscrite']*1000,
                           }
                         })
    logging.info("trying to write {0} points to influxDB".format(len(jsonInflux)))
    try:
        client.write_points(jsonInflux)
    except:
        logging.info("unable to write data points to influxdb")
    else:
        logging.info("done")
