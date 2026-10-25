"""Sunrise and sunset from latitude/longitude (NOAA approximation, about a minute off)."""
import math
from datetime import date, datetime, timedelta, timezone

_J2000 = 2451545.0
_EPOCH = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)


def sun_times(day, lat, lon):
    """(sunrise, sunset) in UTC for `day`, or None during polar day or night."""
    n = day.toordinal() - date(2000, 1, 1).toordinal() + 0.0008
    mean_noon = n - lon / 360
    anomaly = math.radians((357.5291 + 0.98560028 * mean_noon) % 360)
    centre = 1.9148 * math.sin(anomaly) + 0.02 * math.sin(2 * anomaly) + 0.0003 * math.sin(3 * anomaly)
    ecliptic = math.radians((math.degrees(anomaly) + centre + 180 + 102.9372) % 360)
    transit = _J2000 + mean_noon + 0.0053 * math.sin(anomaly) - 0.0069 * math.sin(2 * ecliptic)
    declination = math.asin(math.sin(ecliptic) * math.sin(math.radians(23.4397)))
    cos_hour = ((math.sin(math.radians(-0.833)) - math.sin(math.radians(lat)) * math.sin(declination))
                / (math.cos(math.radians(lat)) * math.cos(declination)))
    if not -1 <= cos_hour <= 1:
        return None
    hour_angle = math.degrees(math.acos(cos_hour)) / 360
    to_datetime = lambda julian: _EPOCH + timedelta(days=julian - _J2000)  # noqa: E731
    return to_datetime(transit - hour_angle), to_datetime(transit + hour_angle)


class Sun:
    """Sun times for the location saved in settings (falls back to LATITUDE/LONGITUDE)."""

    def __init__(self, settings, tz, default_location=None):
        self._settings = settings
        self._tz = tz
        self._default = default_location

    @property
    def location(self):
        return self._settings.data.get("location") or self._default

    def times(self, day):
        """Local (sunrise, sunset) for `day`, or None without a location."""
        location = self.location
        if not location:
            return None
        result = sun_times(day, location["lat"], location["lon"])
        return result and tuple(t.astimezone(self._tz) for t in result)

    def today(self):
        times = self.times(datetime.now(self._tz).date())
        return times and {"sunrise": times[0].strftime("%H:%M"), "sunset": times[1].strftime("%H:%M")}
