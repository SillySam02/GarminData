#!/usr/bin/env python

#
# copyright Tom Goetz
#

import os, sys, getopt, re, string, logging, datetime, calendar

import HealthDB
import GarminDB


root_logger = logging.getLogger()
logger = logging.getLogger(__file__)

class MovingAverageFilter():
    def __init__(self, factor, initial_value):
        self.factor1 = factor
        self.factor2 = 1.0 - factor
        self.value = initial_value

    def filter(self, input_value):
        self.value = (self.value * self.factor1) + (input_value * self.factor2)
        return round(self.value)


class Analyze():
    def __init__(self, db_params_dict):
        self.garmindb = GarminDB.GarminDB(db_params_dict)
        self.mondb = GarminDB.MonitoringDB(db_params_dict)
        self.garminsumdb = GarminDB.GarminSummaryDB(db_params_dict)
        self.sumdb = HealthDB.SummaryDB(db_params_dict)
        units = GarminDB.Attributes.get(self.garmindb, 'units')
        if units == 'english':
            self.english_units = True
        else:
            self.english_units = False

    def set_sleep_period(self, sleep_period_start, sleep_period_stop):
        GarminDB.Attributes.set(self.garmindb, 'sleep_period_start', sleep_period_start)
        GarminDB.Attributes.set(self.garmindb, 'sleep_period_stop', sleep_period_stop)

    def get_years(self):
        years = GarminDB.Monitoring.get_years(self.mondb)
        GarminDB.Summary.set(self.garminsumdb, 'years', len(years))
        logger.info("Years (%d): %s" % (len(years), str(years)))
        for year in years:
            self.get_months(year)
            self.get_days(year)

    def get_months(self, year):
        months = GarminDB.Monitoring.get_month_names(self.mondb, year)
        GarminDB.Summary.set(self.garminsumdb, str(year) + '_months', len(months))
        logger.info("%s Months (%s): %s" % (year, len(months) , str(months)))

    def get_days(self, year):
        days = GarminDB.Monitoring.get_days(self.mondb, year)
        days_count = len(days)
        if days_count > 0:
            first_day = days[0]
            last_day = days[-1]
            span = last_day - first_day + 1
        else:
            span = 0
        GarminDB.Summary.set(self.garminsumdb, str(year) + '_days', days_count)
        GarminDB.Summary.set(self.garminsumdb, str(year) + '_days_span', span)
        logger.info("%d Days (%d vs %d): %s" % (year, days_count, span, str(days)))
        for index in xrange(days_count - 1):
            day = int(days[index])
            next_day = int(days[index + 1])
            if next_day != day + 1:
                day_str = str(HealthDB.day_of_the_year_to_datetime(year, day))
                next_day_str = str(HealthDB.day_of_the_year_to_datetime(year, next_day))
                logger.info("Days gap between %d (%s) and %d (%s)" % (day, day_str, next_day, next_day_str))

    base_awake_intensity = 3
    base_active_intensity = 10

    sleep_state = {
        0 : 'deep_sleep',
        1 : 'light_sleep',
        2 : 'light_sleep',
        3 : 'awake',
        4 : 'awake',
        5 : 'awake',
        6 : 'awake',
        7 : 'awake',
        8 : 'awake',
        9 : 'active',
        10 : 'active',
        11 : 'active',
        12 : 'moderately_active',
        13 : 'moderately_active',
        14 : 'moderately_active',
        15 : 'very_active',
        16 : 'very_active',
        17 : 'very_active',
        18 : 'extremely_active',
        18 : 'extremely_active',
        19 : 'extremely_active',
    }

    sleep_state_index = {
        'deep_sleep' : 0,
        'light_sleep' : 1,
        'awake' : 2,
        'active' : 3,
        'moderately_active' : 4,
        'very_active' : 5,
        'extremely_active' : 6
    }

    sleep_state_latch_time = {
        'deep_sleep' : 300,
        'light_sleep' : 120,
        'awake' : 60,
        'active' : 60,
        'moderately_active' : 60,
        'very_active' : 60,
        'extremely_active' : 60
    }


    def sleep_state_change(self, sleep_state_ts, sleep_state, sleep_state_duration):
        GarminDB.Sleep.create_or_update(self.garminsumdb, {'timestamp' : sleep_state_ts, 'event' : sleep_state, 'duration' : sleep_state_duration})
        if self.bedtime_ts is None:
            if self.sleep_state_index[sleep_state] <= 1 and sleep_state_duration >= 600:
                self.bedtime_ts = sleep_state_ts - datetime.timedelta(0, 1)
        elif self.wake_ts is None:
            sleep_duration = int((sleep_state_ts - self.bedtime_ts).total_seconds())
            if self.sleep_state_index[sleep_state] >= 2 and sleep_state_duration >= 600 and sleep_duration >= 7200:
                self.wake_ts = sleep_state_ts + datetime.timedelta(0, 1)

    def calculate_sleep(self, day_date, sleep_period_start, sleep_period_stop):
        generic_act_id = GarminDB.ActivityType.get_id(self.mondb, 'generic')
        stop_act_id = GarminDB.ActivityType.get_id(self.mondb, 'stop_disable')

        sleep_search_start_ts = datetime.datetime.combine(day_date, sleep_period_start) - datetime.timedelta(0, 0, 0, 0, 0, 2)
        sleep_search_stop_ts = datetime.datetime.combine(day_date + datetime.timedelta(1), sleep_period_stop) + datetime.timedelta(0, 0, 0, 0, 0, 2)

        activity = GarminDB.Monitoring.get_activity(self.mondb, sleep_search_start_ts, sleep_search_stop_ts)

        initial_intensity = self.base_awake_intensity
        last_intensity = initial_intensity
        last_sample_ts = sleep_search_stop_ts
        activity_periods = []
        for index in xrange(len(activity) - 1, 0, -1):
            (timestamp, activity_type_id, intensity) = activity[index]
            duration = int((last_sample_ts - timestamp).total_seconds())
            if activity_type_id != stop_act_id:
                if intensity is None:
                    intensity = self.base_active_intensity
                else:
                    intensity = self.base_active_intensity + (intensity * 2)
            activity_periods.insert(0, (timestamp, last_intensity, duration))
            last_intensity = intensity
            last_sample_ts = timestamp

        self.bedtime_ts = None
        self.wake_ts = None
        prev_sleep_state = self.sleep_state[initial_intensity]
        prev_sleep_state_ts = sleep_search_start_ts
        mov_avg_flt = MovingAverageFilter(0.85, initial_intensity)
        for period_index, (timestamp, intensity, duration) in enumerate(activity_periods):
            for sec_index in xrange(0, duration, 60):
                filtered_intensity = mov_avg_flt.filter(intensity)
                sleep_state = self.sleep_state[filtered_intensity]
                current_ts = timestamp + datetime.timedelta(0, sec_index)
                duration = int((current_ts - prev_sleep_state_ts).total_seconds())
                if sleep_state != prev_sleep_state and duration >= self.sleep_state_latch_time[prev_sleep_state]:
                    self.sleep_state_change(prev_sleep_state_ts, prev_sleep_state, duration)
                    prev_sleep_state = sleep_state
                    prev_sleep_state_ts = current_ts
        self.sleep_state_change(prev_sleep_state_ts, prev_sleep_state, duration)
        GarminDB.Sleep.create_or_update(self.garminsumdb, {'timestamp' : self.bedtime_ts, 'event' : 'bed_time', 'duration' : 1})
        GarminDB.Sleep.create_or_update(self.garminsumdb, {'timestamp' : self.wake_ts, 'event' : 'wake_time', 'duration' : 1})

    def calculate_resting_heartrate(self, day_date, sleep_period_stop):
        start_ts = datetime.datetime.combine(day_date, sleep_period_stop)
        rhr = GarminDB.MonitoringHeartRate.get_resting_heartrate(self.mondb, start_ts)
        if rhr:
            GarminDB.RestingHeartRate.create_or_update(self.garminsumdb, {'day' : day_date, 'resting_heart_rate' : rhr})

    def calculate_day_stats(self, day_date):
        stats = GarminDB.MonitoringHeartRate.get_daily_stats(self.mondb, day_date)
        stats.update(GarminDB.RestingHeartRate.get_daily_stats(self.garminsumdb, day_date))
        stats.update(GarminDB.Weight.get_daily_stats(self.garmindb, day_date))
        stats.update(GarminDB.Stress.get_daily_stats(self.garmindb, day_date))
        stats.update(GarminDB.MonitoringClimb.get_daily_stats(self.mondb, day_date, self.english_units))
        stats.update(GarminDB.MonitoringIntensityMins.get_daily_stats(self.mondb, day_date))
        stats.update(GarminDB.Monitoring.get_daily_stats(self.mondb, day_date))
        GarminDB.DaysSummary.create_or_update(self.garminsumdb, stats)

    def calculate_week_stats(self, day_date):
        stats = GarminDB.MonitoringHeartRate.get_weekly_stats(self.mondb, day_date)
        stats.update(GarminDB.RestingHeartRate.get_weekly_stats(self.garminsumdb, day_date))
        stats.update(GarminDB.Weight.get_weekly_stats(self.garmindb, day_date))
        stats.update(GarminDB.Stress.get_weekly_stats(self.garmindb, day_date))
        stats.update(GarminDB.MonitoringClimb.get_weekly_stats(self.mondb, day_date, self.english_units))
        stats.update(GarminDB.MonitoringIntensityMins.get_weekly_stats(self.mondb, day_date))
        stats.update(GarminDB.Monitoring.get_weekly_stats(self.mondb, day_date))
        GarminDB.WeeksSummary.create_or_update(self.garminsumdb, stats)
        HealthDB.WeeksSummary.create_or_update(self.sumdb, stats)

    def calculate_month_stats(self, start_day_date, end_day_date):
        stats = GarminDB.MonitoringHeartRate.get_monthly_stats(self.mondb, start_day_date, end_day_date)
        stats.update(GarminDB.RestingHeartRate.get_monthly_stats(self.garminsumdb, start_day_date, end_day_date))
        stats.update(GarminDB.Weight.get_monthly_stats(self.garmindb, start_day_date, end_day_date))
        stats.update(GarminDB.Stress.get_monthly_stats(self.garmindb, start_day_date, end_day_date))
        stats.update(GarminDB.MonitoringClimb.get_monthly_stats(self.mondb, start_day_date, end_day_date, self.english_units))
        stats.update(GarminDB.MonitoringIntensityMins.get_monthly_stats(self.mondb, start_day_date, end_day_date))
        stats.update(GarminDB.Monitoring.get_monthly_stats(self.mondb, start_day_date, end_day_date))
        GarminDB.MonthsSummary.create_or_update(self.garminsumdb, stats)
        HealthDB.MonthsSummary.create_or_update(self.sumdb, stats)

    def summary(self):
        sleep_period_start = GarminDB.Attributes.get_time(self.garmindb, 'sleep_period_start')
        sleep_period_stop = GarminDB.Attributes.get_time(self.garmindb, 'sleep_period_stop')

        years = GarminDB.Monitoring.get_years(self.mondb)
        for year in years:
            days = GarminDB.Monitoring.get_days(self.mondb, year)
            for day in days:
                day_date = datetime.date(year, 1, 1) + datetime.timedelta(day - 1)
                self.calculate_sleep(day_date, sleep_period_start, sleep_period_stop)
                self.calculate_resting_heartrate(day_date, sleep_period_stop)
                self.calculate_day_stats(day_date)

            for week_starting_day in xrange(1, 365, 7):
                day_date = datetime.date(year, 1, 1) + datetime.timedelta(week_starting_day - 1)
                self.calculate_week_stats(day_date)

            for month in xrange(1, 12):
                start_day_date = datetime.date(year, month, 1)
                end_day_date = datetime.date(year, month, calendar.monthrange(year, month)[1])
                self.calculate_month_stats(start_day_date, end_day_date)

def usage(program):
    print '%s -s <sqlite db path> -m ...' % program
    sys.exit()

def main(argv):
    debug = False
    db_params_dict = {}
    dates = False
    sleep_period_start = None
    sleep_period_stop = None

    try:
        opts, args = getopt.getopt(argv,"di:ts:", ["debug", "dates", "mysql=", "sleep=", "sqlite="])
    except getopt.GetoptError:
        usage(sys.argv[0])

    for opt, arg in opts:
        if opt == '-h':
            usage(sys.argv[0])
        elif opt in ("-t", "--debug"):
            logging.debug("debug: True")
            debug = True
        elif opt in ("-d", "--dates"):
            logging.debug("Dates")
            dates = True
        elif opt in ("-S", "--sleep"):
            logging.debug("Sleep: " + arg)
            sleep_args = arg.split(',')
            sleep_period_start = datetime.datetime.strptime(sleep_args[0], "%H:%M").time()
            sleep_period_stop = datetime.datetime.strptime(sleep_args[1], "%H:%M").time()
        elif opt in ("-s", "--summary"):
            logging.debug("Summary")
            summary = True
        elif opt in ("-s", "--sqlite"):
            logging.debug("Sqlite DB path: %s" % arg)
            db_params_dict['db_type'] = 'sqlite'
            db_params_dict['db_path'] = arg
        elif opt in ("--mysql"):
            logging.debug("Mysql DB string: %s" % arg)
            db_args = arg.split(',')
            db_params_dict['db_type'] = 'mysql'
            db_params_dict['db_username'] = db_args[0]
            db_params_dict['db_password'] = db_args[1]
            db_params_dict['db_host'] = db_args[2]

    if debug:
        root_logger.setLevel(logging.DEBUG)
    else:
        root_logger.setLevel(logging.INFO)

    if len(db_params_dict) == 0:
        print "Missing arguments:"
        usage(sys.argv[0])

    analyze = Analyze(db_params_dict)
    if sleep_period_start and sleep_period_stop:
        analyze.set_sleep_period(sleep_period_start, sleep_period_stop)
    if dates:
        analyze.get_years()
    analyze.summary()

if __name__ == "__main__":
    main(sys.argv[1:])


