#!/usr/bin/env python3

"""
Accurate timing of Strava segments
"""

__author__ = "Tobias Hermann"
__copyright__ = "Copyright 2023, Tobias Hermann"
__license__ = "MIT"
__email__ = "editgym@gmail.com"

import argparse
import datetime
from typing import List, Optional, TypeVar, Tuple

import requests
from sympy.geometry import Segment, Point, Line
from tcxreader.tcxreader import TCXReader, TCXTrackPoint


def log_msg(msg: str) -> None:
    """Generic console logging with timestamp."""
    print(f'{datetime.datetime.now()}: {msg}', flush=True)


def get_segment(access_token: str, segment_id: int) -> Segment:
    """Download segment data using the Strava API"""

    # Hardcoded segment IDs, so one does not always need a valid access token.
    if segment_id == 4391619:  # Marienfeld Climb
        return Segment(Point(7.436902, 50.884516), Point(7.441928, 50.883243))

    log_msg(f'Loading data for segment: {segment_id}')
    url = f'https://www.strava.com/api/v3/segments/{segment_id}'
    response = requests.get(url,
                            headers={'Authorization': f'Bearer {access_token}'},
                            timeout=10
                            ).json()
    log_msg(response)
    start_lat, start_lng = response['start_latlng']
    end_lat, end_lng = response['end_latlng']
    name = response['name']
    log_msg(f'Loaded segment: {name}')
    return Segment(Point(start_lng, start_lat), Point(end_lng, end_lat))


def track_point_to_point(trackpoint: TCXTrackPoint) -> Point:
    """As an approximation for small distances,
    we assume latitude and longitude to be a Euclidean space.
    Close to the earth's poles, this would not be ok."""
    return Point(trackpoint.longitude, trackpoint.latitude)


def check_passed_point(step: Segment, point: Point) -> bool:
    """For the segment creator, or for start/end points in a curve,
    the point itself might be an exact match.
    Maybe consider processing two steps at a time."""
    return bool(step.distance(point) < min(step.p1.distance(point), step.p2.distance(point)))


def project_and_interpolate(step: Segment,
                            step_t1: datetime.datetime,
                            step_t2: datetime.datetime,
                            point: Point) -> Optional[TCXTrackPoint]:
    """Find the closest point on a step (line segment) and interpolate the timestamp."""
    start_projection = Line(step.p1, step.p2).projection(point)
    start_step_fraction = float(start_projection.distance(point) / step.length)
    step_duration_s = (step_t2 - step_t1).total_seconds()
    dt_s = start_step_fraction * step_duration_s
    exact_time = step_t1 + datetime.timedelta(seconds=dt_s)
    return TCXTrackPoint(
        longitude=float(start_projection.x),
        latitude=float(start_projection.y),
        time=exact_time)


def calc_segment_times(segment: Segment, activity: List[TCXTrackPoint]) -> List[float]:
    """
    Use Interpolation to calculate the precise effort times.
    One can pass all activity trackpoints, but for performance
    it makes sense to pre-filter and only pass the relevant ones.
    However, we should have at least 2 points close to the segment start
    and 2 points close to the segment end.
    """
    start: Optional[TCXTrackPoint] = None
    end: Optional[TCXTrackPoint] = None
    effort_times: List[float] = []
    for point_idx in range(len(activity[:-2])):
        ap1 = activity[point_idx]
        ap2 = activity[point_idx + 1]
        step = Segment(track_point_to_point(ap1), track_point_to_point(ap2))
        if check_passed_point(step, segment.p1):
            start = project_and_interpolate(step, ap1.time, ap2.time, segment.p1)
        if start:
            if check_passed_point(step, segment.p2):
                end = project_and_interpolate(step, ap1.time, ap2.time, segment.p2)
        if start and end:
            effort_times.append((end.time - start.time).total_seconds())
            start, end = None, None
    return effort_times


def is_trackpoint_close_to_point(trackpoint: TCXTrackPoint, point: Point) -> bool:
    """For performance, we simply compare latitude and longitude.
    An actual implementation would do probably something
    that also works on the earth's poles."""
    return bool( \
        float(point.y) - 0.0005 <= trackpoint.latitude <= float(point.y) + 0.0005 and \
        float(point.x) - 0.0005 <= trackpoint.longitude <= float(point.x) + 0.0005)


def find_indexes_of_trackpoints_closest_to_segment_start_or_and(
        segment: Segment, trackpoints: List[TCXTrackPoint]) -> Tuple[int, int]:
    """
    This could be the replaced by any other (probably already existing) way
    of finding the closes points.
    """
    invalid_idx = -1
    invalid_distance = 99999999.9
    start_idx_dist: Tuple[int, float] = invalid_idx, invalid_distance
    end_idx_dist: Tuple[int, float] = invalid_idx, invalid_distance
    for point_idx, trackpoint in enumerate(trackpoints):

        # Find start of effort first.
        if is_trackpoint_close_to_point(trackpoints[point_idx], segment.p1):
            start_dist = track_point_to_point(trackpoint).distance(segment.p1)
            if start_idx_dist[0] == invalid_idx or start_dist < start_idx_dist[1]:
                start_idx_dist = point_idx, start_dist

        # Only consider end points if they came after a start point.
        if start_idx_dist[0] != invalid_idx and \
                is_trackpoint_close_to_point(trackpoints[point_idx], segment.p2):
            end_dist = track_point_to_point(trackpoint).distance(segment.p2)
            if not end_idx_dist or end_dist < end_idx_dist[1]:
                end_idx_dist = point_idx, end_dist

    if not start_idx_dist:
        raise RuntimeError("Did not find a suitable segment start point in the acticity.")
    if not end_idx_dist:
        raise RuntimeError("Did not find a suitable segment end point in the acticity.")
    return start_idx_dist[0], end_idx_dist[0]


T = TypeVar('T')


def flatten_list(nested_list: List[List[T]]) -> List[T]:
    """Concatenate sublists."""
    return [item for sublist in nested_list for item in sublist]


def with_surrounding_trackpoints(
        trackpoints: List[TCXTrackPoint],
        center_idx: int) -> List[TCXTrackPoint]:
    """Get trackpoint surrounding a center one."""
    all_idxs = [center_idx - 2, center_idx - 1, center_idx, center_idx + 1, center_idx + 2]
    valid_idxs = sorted(list(set((filter(lambda idx: 0 <= idx < len(trackpoints), all_idxs)))))
    return [trackpoints[idx] for idx in valid_idxs]


def segment_time(activity_tcx_path: str, segment: Segment) -> None:
    """Calculate the effort time of an activity on a specific segment."""
    tcx_reader = TCXReader()
    activity = tcx_reader.read(activity_tcx_path)
    log_msg(f'Analyzing activity: {activity_tcx_path}')

    trackpoints: List[TCXTrackPoint] = activity.trackpoints

    start_idx, end_ids = find_indexes_of_trackpoints_closest_to_segment_start_or_and(
        segment, trackpoints)

    relevant_trackpoints = \
        with_surrounding_trackpoints(trackpoints, start_idx) \
        + with_surrounding_trackpoints(trackpoints, end_ids)

    segment_times = calc_segment_times(segment, relevant_trackpoints)
    log_msg(f'Segment times: {segment_times=}')


def main() -> None:
    """Parse command line and run calculation."""
    parser = argparse.ArgumentParser('AccurateTimingOfStravaSegments')
    parser.add_argument('-a', '--activity_tcx_file',
                        help='Use Sauce for Strava™ to export TCX files.')
    parser.add_argument('-s', '--segment_id', type=int,
                        help='Can be copied from the URL in the browser.')
    parser.add_argument('-t', '--access_token',
                        help='See: https://developers.strava.com/docs/authentication/')
    args = parser.parse_args()
    segment_time(args.activity_tcx_file, get_segment(args.access_token, args.segment_id))


if __name__ == '__main__':
    main()
