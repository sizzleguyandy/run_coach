"""
gpx_parser.py — GPX file parser using the standard library only.

Handles the Garmin TrackPointExtension namespace for HR/cadence, which
is where most consumer devices (Garmin, Zepp/Amazfit, etc.) put that
data rather than in bare GPX fields. If a file uses a different
extensions schema, hr/cadence will come back None per-point rather
than raising -- check parser_notes on the result.

Usage:
    from gpx_parser import parse_gpx
    summary = parse_gpx("run.gpx")
"""
import math
import xml.etree.ElementTree as ET
from datetime import datetime

GPX_NS = '{http://www.topografix.com/GPX/1/1}'
TPX_NS = '{http://www.garmin.com/xmlschemas/TrackPointExtension/v1}'


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def parse_gpx(path):
    """Parse a GPX file and return a summary dict matching the
    run_log table schema, mirroring fit_parser.parse_fit()'s shape
    so callers can treat both formats identically after parsing."""
    tree = ET.parse(path)
    root = tree.getroot()

    points = []
    for trkpt in root.iter(GPX_NS + 'trkpt'):
        lat = float(trkpt.get('lat'))
        lon = float(trkpt.get('lon'))
        ele_el = trkpt.find(GPX_NS + 'ele')
        time_el = trkpt.find(GPX_NS + 'time')
        ele = float(ele_el.text) if ele_el is not None else None
        t = datetime.fromisoformat(time_el.text.replace('Z', '+00:00')) if time_el is not None else None
        hr = cad = None
        ext = trkpt.find(GPX_NS + 'extensions')
        if ext is not None:
            tpx = ext.find(TPX_NS + 'TrackPointExtension')
            if tpx is not None:
                hr_el = tpx.find(TPX_NS + 'hr')
                cd_el = tpx.find(TPX_NS + 'cad')
                hr = int(hr_el.text) if hr_el is not None else None
                cad = float(cd_el.text) if cd_el is not None else None
        points.append({'lat': lat, 'lon': lon, 'ele': ele, 't': t, 'hr': hr, 'cad': cad})

    notes = []
    if not points:
        return {'parser_notes': 'No trkpt elements found in file.', 'points': []}
    if not any(p['t'] for p in points):
        notes.append('no timestamps found -- cannot compute pace or duration')
    if not any(p['hr'] for p in points):
        notes.append('no heart rate data found (checked Garmin TrackPointExtension namespace)')

    dist_m = 0.0
    gain = loss = 0.0
    cum = [0.0]
    prev = None
    for p in points:
        if prev is not None:
            d = _haversine(prev['lat'], prev['lon'], p['lat'], p['lon'])
            dist_m += d
            if prev['ele'] is not None and p['ele'] is not None:
                de = p['ele'] - prev['ele']
                if de > 0:
                    gain += de
                else:
                    loss += -de
        cum.append(dist_m)
        prev = p

    start_t, end_t = points[0]['t'], points[-1]['t']
    elapsed_min = (end_t - start_t).total_seconds() / 60.0 if (start_t and end_t) else None

    moving_sec = 0.0
    for i in range(1, len(points)):
        if points[i]['t'] and points[i - 1]['t']:
            dt = (points[i]['t'] - points[i - 1]['t']).total_seconds()
            seg_d = cum[i] - cum[i - 1]
            if dt > 0 and (seg_d / dt) > 0.3:
                moving_sec += dt
    moving_min = moving_sec / 60.0

    hrs = [p['hr'] for p in points if p['hr']]
    avg_hr = sum(hrs) / len(hrs) if hrs else None
    max_hr = max(hrs) if hrs else None

    total_dist_km = dist_m / 1000.0
    avg_pace_sec_per_km = (moving_sec / total_dist_km) if total_dist_km else None

    splits = []
    marker = 1000.0
    seg_start_t = points[0]['t']
    seg_hrs = []
    km_num = 1
    for i, p in enumerate(points):
        if p['hr']:
            seg_hrs.append(p['hr'])
        if cum[i] >= marker and seg_start_t is not None and p['t'] is not None:
            dt = (p['t'] - seg_start_t).total_seconds()
            splits.append({
                'km': km_num, 'time_sec': dt,
                'avg_hr': (sum(seg_hrs) / len(seg_hrs)) if seg_hrs else None,
                'max_hr': max(seg_hrs) if seg_hrs else None,
            })
            marker += 1000.0
            seg_start_t = p['t']
            seg_hrs = []
            km_num += 1

    first_half_avg_hr = second_half_avg_hr = hr_drift = None
    if len(hrs) >= 20:
        mid = len(hrs) // 2
        fh, sh = hrs[:mid], hrs[mid:]
        first_half_avg_hr = sum(fh) / len(fh)
        second_half_avg_hr = sum(sh) / len(sh)
        hr_drift = second_half_avg_hr - first_half_avg_hr

    return {
        'source_format': 'gpx',
        'recorded_at': start_t.isoformat() if start_t else None,
        'distance_km': total_dist_km,
        'elapsed_time_min': elapsed_min,
        'moving_time_min': moving_min,
        'avg_hr': avg_hr,
        'max_hr': max_hr,
        'elevation_gain_m': gain,
        'elevation_loss_m': loss,
        'avg_pace_sec_per_km': avg_pace_sec_per_km,
        'splits': splits,
        'first_half_avg_hr': first_half_avg_hr,
        'second_half_avg_hr': second_half_avg_hr,
        'hr_drift_delta': hr_drift,
        'parser_notes': '; '.join(notes) if notes else None,
        'points': points,
    }


if __name__ == '__main__':
    import json
    import sys

    path = sys.argv[1]
    result = parse_gpx(path)
    result_no_points = {k: v for k, v in result.items() if k != 'points'}
    print(json.dumps(result_no_points, indent=2, default=str))
