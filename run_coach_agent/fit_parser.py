"""
fit_parser.py — minimal pure-python FIT file parser.

No external dependencies. Handles definition + data messages, both
endiannesses, and developer-data fields (the thing that silently
breaks naive parsers -- see NOTE below).

Extracts 'record' messages (global_mesg_num == 20): timestamp,
position, altitude, heart_rate, cadence, distance, speed.

NOTE on developer-data fields: a definition message header has this
layout: bit 7 = compressed timestamp, bit 6 = is_definition, bit 5 =
has_developer_fields, bits 4-0 = local message type. Many real-world
FIT encoders (this includes Zepp's app, confirmed against real
files) set bit 5 on some definition messages. If you don't check for
it, you'll read past a chunk of developer-field-definition bytes as
if it were the next message header, silently misalign the rest of
the file, and get zero (or garbage) records with no error thrown.
This parser checks header bit 0x20 explicitly and skips those bytes.

Usage:
    from fit_parser import parse_fit
    summary = parse_fit("run.fit")
    # summary is a dict matching the run_log table columns
"""
import struct
from datetime import datetime, timedelta, timezone

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
RECORD_MESG_NUM = 20

BASE_TYPES = {
    0x00: ('B', 1, None),        # enum
    0x01: ('b', 1, 0x7F),        # sint8
    0x02: ('B', 1, 0xFF),        # uint8
    0x83: ('h', 2, 0x7FFF),      # sint16
    0x84: ('H', 2, 0xFFFF),      # uint16
    0x85: ('i', 4, 0x7FFFFFFF),  # sint32
    0x86: ('I', 4, 0xFFFFFFFF),  # uint32
    0x07: (None, 1, None),       # string
    0x88: ('f', 4, None),        # float32
    0x89: ('d', 8, None),        # float64
    0x0A: ('B', 1, 0xFF),        # uint8z
    0x8B: ('H', 2, 0xFFFF),      # uint16z
    0x8C: ('I', 4, 0xFFFFFFFF),  # uint32z
    0x0D: ('B', 1, None),        # byte
    0x8E: ('q', 8, None),        # sint64
    0x8F: ('Q', 8, None),        # uint64
    0x90: ('Q', 8, None),        # uint64z
}

FIELDS = {
    253: 'timestamp', 0: 'position_lat', 1: 'position_long',
    2: 'altitude', 3: 'heart_rate', 4: 'cadence', 5: 'distance',
    6: 'speed', 73: 'enhanced_speed', 78: 'enhanced_altitude',
}


def _raw_records(path):
    """Parse the FIT binary into a list of {field_name: value} dicts
    for every 'record' message. Internal — see parse_fit() for the
    summary interface most callers want."""
    with open(path, 'rb') as f:
        data = f.read()

    header_size = data[0]
    pos = header_size
    end = len(data) - 2  # trailing 2-byte CRC

    local_defs = {}
    records = []

    while pos < end:
        header = data[pos]; pos += 1
        compressed_ts = bool(header & 0x80)
        is_def = bool(header & 0x40)
        has_dev_fields = bool(header & 0x20)
        local_type = header & 0x0F if not compressed_ts else (header & 0x60) >> 5

        if is_def:
            reserved = data[pos]; arch = data[pos + 1]; pos += 2
            endian = '>' if arch == 1 else '<'
            global_num = struct.unpack_from(endian + 'H', data, pos)[0]; pos += 2
            num_fields = data[pos]; pos += 1
            fields = []
            for _ in range(num_fields):
                fnum, fsize, base = data[pos], data[pos + 1], data[pos + 2]
                pos += 3
                fields.append((fnum, fsize, base))
            dev_field_sizes = []
            if has_dev_fields:
                n_dev = data[pos]; pos += 1
                for _ in range(n_dev):
                    dev_size = data[pos + 1]
                    pos += 3
                    dev_field_sizes.append(dev_size)
            local_defs[local_type] = {
                'endian': endian, 'global_num': global_num,
                'fields': fields, 'dev_field_sizes': dev_field_sizes,
            }
        else:
            d = local_defs.get(local_type)
            if d is None:
                break  # malformed or unsupported stream -- stop rather than guess
            endian = d['endian']
            rec = {}
            for fnum, fsize, base in d['fields']:
                fmt, natsize, invalid = BASE_TYPES.get(base, (None, fsize, None))
                raw = data[pos:pos + fsize]
                pos += fsize
                if fmt is None or natsize != fsize:
                    val = raw
                else:
                    val = struct.unpack(endian + fmt, raw)[0]
                    if invalid is not None and val == invalid:
                        val = None
                if d['global_num'] == RECORD_MESG_NUM and fnum in FIELDS:
                    rec[FIELDS[fnum]] = val
            for dev_size in d['dev_field_sizes']:
                pos += dev_size
            if d['global_num'] == RECORD_MESG_NUM and rec:
                records.append(rec)

    return records


def parse_fit(path):
    """Parse a FIT file and return a summary dict matching the
    run_log table schema, plus a 'splits' list and the raw per-second
    points (for callers that want to re-derive anything).

    Returns None-valued summary fields (not an exception) when data
    is missing, so callers can decide how to handle a partial file --
    a coaching agent should surface that as a data-quality note, not
    silently compute misleading averages.
    """
    raw = _raw_records(path)

    def ts_to_dt(v):
        return FIT_EPOCH + timedelta(seconds=v) if v is not None else None

    points = []
    for r in raw:
        t = ts_to_dt(r.get('timestamp'))
        if t is None:
            continue
        dist = r.get('distance')
        dist = dist / 100.0 if dist is not None else None
        alt = r.get('enhanced_altitude', r.get('altitude'))
        if alt is not None:
            alt = alt / 5.0 - 500.0
        speed = r.get('enhanced_speed', r.get('speed'))
        if speed is not None:
            speed = speed / 1000.0
        points.append({
            't': t, 'hr': r.get('heart_rate'), 'dist_m': dist,
            'alt_m': alt, 'speed_ms': speed, 'cad': r.get('cadence'),
        })

    if not points:
        return {'parser_notes': 'No record messages found -- file may be empty, '
                                 'corrupt, or use an unsupported message layout.',
                'points': []}

    points_with_dist = [p for p in points if p['dist_m'] is not None]
    notes = []
    if len(points_with_dist) < len(points) * 0.9:
        notes.append(f"only {len(points_with_dist)}/{len(points)} points had distance data")

    start_t, end_t = points[0]['t'], points[-1]['t']
    elapsed_min = (end_t - start_t).total_seconds() / 60.0
    total_dist_km = (max(p['dist_m'] for p in points_with_dist) / 1000.0) if points_with_dist else None

    moving_sec = 0.0
    prev = None
    for p in points:
        if prev is not None and p['speed_ms'] is not None:
            dt = (p['t'] - prev['t']).total_seconds()
            if p['speed_ms'] > 0.3:
                moving_sec += dt
        prev = p
    moving_min = moving_sec / 60.0

    hrs = [p['hr'] for p in points if p['hr']]
    avg_hr = sum(hrs) / len(hrs) if hrs else None
    max_hr = max(hrs) if hrs else None
    if not hrs:
        notes.append('no heart rate data in file')

    gain = loss = 0.0
    prev_alt = None
    for p in points:
        if p['alt_m'] is not None:
            if prev_alt is not None:
                de = p['alt_m'] - prev_alt
                if de > 0:
                    gain += de
                else:
                    loss += -de
            prev_alt = p['alt_m']

    avg_pace_sec_per_km = (moving_sec / total_dist_km) if total_dist_km else None

    # per-km splits
    splits = []
    if points_with_dist:
        marker = 1000.0
        seg_start_t = points_with_dist[0]['t']
        seg_hrs = []
        km_num = 1
        for p in points_with_dist:
            if p['hr']:
                seg_hrs.append(p['hr'])
            if p['dist_m'] >= marker:
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

    # HR drift: split the whole run in half by point count, compare avg HR.
    # This is the single most useful derived signal -- a whole-run average
    # hides exactly this pattern. See analysis.py for how it's used.
    first_half_avg_hr = second_half_avg_hr = hr_drift = None
    hr_points = [p['hr'] for p in points if p['hr']]
    if len(hr_points) >= 20:
        mid = len(hr_points) // 2
        fh, sh = hr_points[:mid], hr_points[mid:]
        first_half_avg_hr = sum(fh) / len(fh)
        second_half_avg_hr = sum(sh) / len(sh)
        hr_drift = second_half_avg_hr - first_half_avg_hr

    return {
        'source_format': 'fit',
        'recorded_at': start_t.isoformat(),
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
        'points': points,  # raw per-record data, in case a caller needs more
    }


if __name__ == '__main__':
    import json
    import sys

    path = sys.argv[1]
    result = parse_fit(path)
    result_no_points = {k: v for k, v in result.items() if k != 'points'}
    print(json.dumps(result_no_points, indent=2, default=str))
