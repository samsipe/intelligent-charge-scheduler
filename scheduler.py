"""
This module is responsible for scheduling the charging of a Tesla vehicle based on grid
demand and a Google calendar.It fetches the grid status, vehicle status, and optimizes
the vehicle charge accordingly.

Author: Sam Sipe
"""
__version__ = "0.4.2"

from dotenv import load_dotenv
import argparse
import schedule
import pytz
import time
import json
import hashlib
import base64
import datetime
import os
import pandas as pd
from gridstatusio import GridStatusClient
import teslapy
import googlemaps

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.cloud import storage

import plotly.express as px
import plotly.graph_objects as go


load_dotenv()


def auth_tesla():
    """
    This function authenticates with the Tesla API using the account email and oauth.
    If the authentication is successful. It will prompt the user to login via a browser
    if the credentials are not cached.

    Returns:
    - teslapy.Tesla: A teslapy.Tesla object that can be used to interact with the
    Tesla API.
    """
    tesla = teslapy.Tesla(
        os.environ.get("TESLA_ACCOUNT_EMAIL"),
        retry=teslapy.Retry(total=3, status_forcelist=(408, 500, 502, 503, 504)),
        timeout=15,
    )
    if not tesla.authorized:
        print("Use browser to login. Page Not Found will be shown at success.")
        print("Open this URL: " + str(tesla.authorization_url()))
        tesla.fetch_token(
            authorization_response=input("Enter URL after authentication: ")
        )
    return tesla


def auth_google():
    """
    This function authenticates with the Google API using the credentials.json file and
    the user's Google account. If the credentials are not cached, it will prompt the user
    to login via a browser. If the credentials are cached and still valid, it will use
    them to authenticate. If the credentials are expired, it will refresh them.

    Returns:
    - google.oauth2.credentials.Credentials: A Credentials object that can be used to
    interact with the Google API.
    """
    creds = None
    SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(
                bind_addr="0.0.0.0", open_browser=False, port=8081
            )
        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return creds


def get_grid_status(
    api_key=os.environ.get("GRID_STATUS_API_KEY"),
    iso=os.environ.get("GRID_ISO", "PJM"),
    lead_time=66,
    lag_time=6,
    cache_file="grid_status.parquet",
    cache_max_age_hours=4,
):
    """
    This function retrieves the current grid status data for a specified ISO (default
    is PJM) from a GridStatus API. If cached data exists and is less than cache_max_age
    old, it will be loaded instead of fetching new data. The function returns a pandas
    DataFrame containing the grid status data.

    Parameters:
    - api_key (str): API key for the GridStatus API.
    - iso (str): ISO for which to retrieve grid status data (default is PJM).
    - cache_file (str): File path for the cache file.
    - cache_max_age (int): Maximum age of cached data before it is considered stale.

    Returns:
    - pandas.DataFrame: DataFrame containing the grid status data.
    """
    # Check if cached data exists and is less than cache_max_age old
    if os.path.exists(cache_file):
        df = pd.read_parquet(cache_file)
        cache_time = df["interval_start_utc"].min()
        if datetime.datetime.now(pytz.utc) - cache_time < datetime.timedelta(
            hours=cache_max_age_hours + lag_time
        ):
            return df

    # Fetch new data and cache it
    client = GridStatusClient(api_key)
    df = client.get_dataset(
        dataset=iso.lower() + "_load_forecast",
        start=(
            datetime.datetime.now(pytz.utc) - datetime.timedelta(hours=lag_time)
        ).strftime("%Y-%m-%dT%H:%M"),
        end=(
            datetime.datetime.now(pytz.utc) + datetime.timedelta(hours=lead_time)
        ).strftime("%Y-%m-%dT%H:%M"),
    )
    df.to_parquet(cache_file, index=False)
    return df


def plot_grid_status(
    df, vehicle=None, time_zone=os.environ.get("TIME_ZONE", "America/New_York")
):
    """
    Creates a plot of the grid status and vehicle charge data.

    Parameters:
    - df (pandas.DataFrame): DataFrame containing the load forecast data.
    - time_zone (str): Time zone to use for the plot (default is "America/New_York").

    Returns:
    - plotly.graph_objs._figure.Figure: A plotly figure object.
    """
    now = datetime.datetime.now(pytz.timezone(time_zone))
    df["interval_start"] = pd.to_datetime(df["interval_start_utc"]).dt.tz_convert(
        time_zone
    )

    mean_load = df["load_forecast"].mean()
    lower_std_load = mean_load - df["load_forecast"].std()
    upper_std_load = mean_load + df["load_forecast"].std()
    current_load = df.loc[(df["interval_start"] - now).abs().idxmin()]["load_forecast"]
    filtered_df = df[
        (df["load_forecast"] < lower_std_load)
        & (df["load_forecast"].shift(-1) >= lower_std_load)
    ]["interval_start_utc"] - datetime.datetime.now(pytz.utc)
    off_peak_hours_end_time = df["interval_start"][
        filtered_df[filtered_df >= datetime.timedelta(0)].idxmin()
    ]
    scheduled_departure_time = None
    scheduled_charging_start_time = None
    datetime_full_charge = None
    if vehicle is not None:
        if vehicle["charge_state"]["scheduled_departure_time"]:
            scheduled_departure_time = datetime.datetime.fromtimestamp(
                vehicle["charge_state"]["scheduled_departure_time"],
                pytz.timezone(time_zone),
            )
        if vehicle["charge_state"]["scheduled_charging_start_time"]:
            scheduled_charging_start_time = datetime.datetime.fromtimestamp(
                vehicle["charge_state"]["scheduled_charging_start_time"],
                pytz.timezone(time_zone),
            )
        if vehicle["charge_state"]["time_to_full_charge"] > 0:
            datetime_full_charge = now + datetime.timedelta(
                hours=vehicle["charge_state"]["time_to_full_charge"]
            )

    fig = px.line(
        df,
        x="interval_start",
        y="load_forecast",
    )
    fig.add_trace(
        go.Scatter(
            x=df["interval_start"],
            y=[upper_std_load] * len(df),
            mode="lines",
            line=dict(color="white", width=1),
            name="",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["interval_start"],
            y=[mean_load] * len(df),
            mode="lines",
            line=dict(color="white", width=1),
            name="",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["interval_start"],
            y=[lower_std_load] * len(df),
            mode="lines",
            line=dict(color="white", width=1),
            name="",
        )
    )
    fig.add_vrect(
        x0=now,
        x1=now,
        line_color="red",
        annotation_text="Time Now",
        line_width=1,
        annotation_position="top left",
    )
    fig.add_hrect(
        y0=current_load,
        y1=current_load,
        line_color="red",
        annotation_text="Current Load",
        line_width=1,
        annotation_position="top right",
    )
    fig.add_vrect(
        x0=off_peak_hours_end_time,
        x1=off_peak_hours_end_time,
        line_color="green",
        annotation_text="End Off Peak",
        line_width=1,
        annotation_position="top left",
    )
    if (
        scheduled_departure_time
        and vehicle["charge_state"]["charging_state"] != "Disconnected"
    ):
        fig.add_vrect(
            x0=scheduled_departure_time,
            x1=scheduled_departure_time,
            line_color="orange",
            line_width=1,
            annotation_text="Depart",
            annotation_position="bottom left",
        )
    if scheduled_charging_start_time and vehicle["charge_state"][
        "charging_state"
    ] not in ["Complete", "Charging"]:
        fig.add_vrect(
            x0=scheduled_charging_start_time,
            x1=off_peak_hours_end_time,
            fillcolor="green",
            opacity=0.2,
            line_width=0,
            annotation_text="Charge",
            annotation_position="bottom left",
        )
    if datetime_full_charge and vehicle["charge_state"]["charging_state"] == "Charging":
        fig.add_vrect(
            x0=now,
            x1=datetime_full_charge,
            fillcolor="green",
            opacity=0.2,
            line_width=0,
            annotation_text="Charging",
            annotation_position="bottom left",
        )
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(
            title="Time",
            tickformat="%-I:%M %p<br>%a %-d %b",
            fixedrange=True,
            showgrid=True,
        ),
        yaxis=dict(
            title="Forecast Grid Load",
            tickvals=[],
            ticktext=[],
            fixedrange=True,
            showgrid=True,
        ),
        showlegend=False,
    )
    return fig


def get_calendar_events(credentials, hours=24, max_results=10, verbose=False):
    """
    This function retrieves upcoming calendar events from the user's primary calendar
    using the Google Calendar API.

    Parameters:
    - credentials (google.oauth2.credentials.Credentials): The user's Google OAuth2
        credentials.
    - hours (int): The number of hours into the future to retrieve events for. Defaults
        to 24.
    - max_results (int): The maximum number of events to retrieve. Defaults to 10.
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Returns:
    - list: A list of dictionaries representing the retrieved events. Each dictionary
        contains the following keys:
        - 'id': The ID of the event.
        - 'summary': The summary of the event.
        - 'location': The location of the event.
        - 'start': The start time of the event as a datetime object.
        - 'end': The end time of the event as a datetime object.
    """
    try:
        service = build("calendar", "v3", credentials=credentials)

        now = datetime.datetime.utcnow().isoformat() + "Z"  # 'Z' indicates UTC time
        time_limit = (
            datetime.datetime.utcnow() + datetime.timedelta(hours=hours)
        ).isoformat() + "Z"

        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=now,
                timeMax=time_limit,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = events_result.get("items", [])

        if verbose:
            if not events:
                print("No upcoming events found.")
            else:
                for event in events:
                    if "location" in event and "http" not in event["location"]:
                        start = event["start"].get(
                            "dateTime", event["start"].get("date")
                        )
                        location = event["location"].replace("\n", " ")
                        summary = event["summary"]
                        print(f"{start} | {summary} | {location}")

    except HttpError as error:
        print("An error occurred: %s" % error)
    return events


def get_directions(
    origin,
    destination,
    api_key=os.environ.get("GOOGLE_MAPS_API_KEY"),
):
    """
    This function calculates the driving distance and duration between two locations
    using the Google Maps API.

    Parameters:
    - origin (str): The starting location for the driving directions.
    - destination (str): The destination location for the driving directions.
    - api_key (str): The API key for the Google Maps API. Defaults to the value of the
        "GOOGLE_MAPS_API_KEY" environment variable.

    Returns:
    - tuple: A tuple containing the driving distance in miles and the driving duration
        in minutes.
    """
    gmaps = googlemaps.Client(key=api_key)

    # Request directions via driving
    try:
        directions_result = gmaps.directions(origin, destination, mode="driving")
    except Exception:
        return 0, 0

    return (
        round(directions_result[0]["legs"][0]["distance"]["value"] / 1609.34, 1),
        round(directions_result[0]["legs"][0]["duration"]["value"] / 60, 1),
    )


def google_cloud_storage_download(
    bucket_name=os.environ.get("BUCKET_NAME"), verbose=False
):
    """
    Downloads all JSON and Parquet files in a Google Cloud Storage bucket to the current directory.

    Parameters:
    - bucket_name (str): The name of the Google Cloud Storage bucket to upload files to.
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Returns:
    - None
    """
    if bucket_name is None:
        return
    bucket = storage.Client().bucket(bucket_name)
    blobs = bucket.list_blobs()
    for blob in blobs:
        if blob.name.endswith((".json", ".parquet")):
            # only download files if they're newer and different than the local version
            if os.path.exists(blob.name) and blob.md5_hash == base64.b64encode(
                hashlib.md5(open(blob.name, "rb").read()).digest()
            ).decode("utf-8"):
                if verbose:
                    print(f"Skipped {blob.name} because the files are the same")
            elif os.path.exists(
                blob.name
            ) and blob.updated <= datetime.datetime.fromtimestamp(
                os.path.getmtime(blob.name), tz=datetime.timezone.utc
            ):
                if verbose:
                    print(f"Skipped {blob.name} because the local version is newer")
            else:
                blob.download_to_filename(blob.name)
                if verbose:
                    print(f"Downloaded {blob.name} from {bucket_name}")


def google_cloud_storage_upload(
    bucket_name=os.environ.get("BUCKET_NAME"), verbose=False
):
    """
    Uploads all JSON and Parquet files in the current directory to a Google Cloud Storage bucket.

    Parameters:
    - bucket_name (str): The name of the Google Cloud Storage bucket to upload files to.
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Returns:
    - None
    """
    if bucket_name is None:
        return
    bucket = storage.Client().bucket(bucket_name)
    for filename in os.listdir():
        if filename.endswith((".json", ".parquet")):
            # only update files if they're different than the local version
            blob = bucket.get_blob(filename)
            if blob is not None and blob.md5_hash == base64.b64encode(
                hashlib.md5(open(filename, "rb").read()).digest()
            ).decode("utf-8"):
                if verbose:
                    print(f"Skipped {filename} because the files are the same")
            else:
                bucket.blob(filename).upload_from_filename(filename)
                if verbose:
                    print(f"Uploaded {filename} to {bucket_name}")


def get_vehicle_status(tesla, verbose=False):
    """
    This function retrieves the status of the first vehicle in the user's Tesla account.

    Parameters:
    - tesla (teslapy.Tesla): A Tesla object authenticated with the user's Tesla account.
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Returns:
    - tuple: A tuple containing the first vehicle in the user's Tesla account and a
        summary of its status.
    """
    try:
        vehicles = tesla.vehicle_list()
    except teslapy.VehicleError as e:
        print(e)
        return None, None
    vehicle = vehicles[0]
    try:
        last_seen = vehicle.last_seen()
    except ValueError:
        last_seen = "just now"

    summary = f"{vehicle['display_name']} is {vehicle['state']} and was last seen {last_seen} with a {vehicle['charge_state']['battery_level']}% charge"
    if verbose:
        print(summary)
    vehicle["summary"] = summary

    with open("vehicle.json", "w") as vehicle_file:
        vehicle_file.write(json.dumps(vehicle))

    return vehicle, summary


def wake_up(vehicle, verbose=False):
    """
    Function to wake up a Tesla vehicle.

    Args:
    - vehicle (teslapy.Vehicle): A TeslaPy object with dictionary access and API request support
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Raises:
    - teslapy.VehicleError: If there is an error with the vehicle.

    Returns:
    - None
    """
    try:
        vehicle.sync_wake_up()
        if verbose:
            print(vehicle["display_name"] + " is now " + vehicle["state"])
    except Exception as e:
        print(e)


def set_charge_current(df, vehicle, verbose=False):
    """
    This function sets the charging current of a Tesla vehicle.

    Parameters:
    - vehicle (teslapy.Vehicle): A TeslaPy object with dictionary access and API request support
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Raises:
    - teslapy.VehicleError: If there is an error with the vehicle.

    Returns:
    - None
    """
    try:
        vehicle.command(
            "CHARGING_AMPS",
            charging_amps=vehicle["charge_state"]["charge_current_request"],
        )
        if verbose:
            print(
                f"Charging amps set to {vehicle['charge_state']['charge_current_request']}A"
            )
    except teslapy.VehicleError as e:
        print(e)
    except Exception as e:
        print(e)


def set_charge_limit(vehicle, verbose=False):
    """
    This function sets the charge limit of a Tesla vehicle.

    Parameters:
    - vehicle (teslapy.Vehicle): A TeslaPy object with dictionary access and API request support
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Raises:
    - teslapy.VehicleError: If there is an error with the vehicle.

    Returns:
    - None
    """
    try:
        vehicle.command(
            "CHANGE_CHARGE_LIMIT", percent=vehicle["charge_state"]["charge_limit_soc"]
        )
        if verbose:
            print(f"Charge limit set to {vehicle['charge_state']['charge_limit_soc']}%")
    except teslapy.VehicleError as e:
        if verbose:
            print("Charge limit " + str(e))
    except Exception as e:
        print(e)


def set_start_charging(vehicle, verbose=False):
    """
    This function starts charging a Tesla vehicle.

    Parameters:
    - vehicle (teslapy.Vehicle): A TeslaPy object with dictionary access and API request support
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Raises:
    - teslapy.VehicleError: If there is an error with the vehicle.

    Returns:
    - None
    """
    try:
        vehicle.command("START_CHARGE")
        if verbose:
            print("Charging started")
    except Exception as e:
        print(e)


def set_stop_charging(vehicle, verbose=False):
    """
    This function stops charging a Tesla vehicle.

    Parameters:
    - vehicle (teslapy.Vehicle): A TeslaPy object with dictionary access and API request support
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Raises:
    - teslapy.VehicleError: If there is an error with the vehicle.

    Returns:
    - None
    """
    try:
        vehicle.command("STOP_CHARGE")
        if verbose:
            print("Charging stopped")
    except Exception as e:
        print(e)


def set_schedule(vehicle, verbose=False):
    """
    This function sets the scheduled departure time and charging settings for a
    Tesla vehicle.

    Parameters:
    - vehicle (teslapy.Vehicle): A TeslaPy object with dictionary access and API request support
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Raises:
    - teslapy.VehicleError: If there is an error with the vehicle.

    Returns:
    - None
    """
    try:
        vehicle.command(
            "SCHEDULED_DEPARTURE",
            enable=vehicle["charge_state"]["off_peak_charging_enabled"],
            departure_time=vehicle["charge_state"]["scheduled_departure_time_minutes"],
            preconditioning_enabled=vehicle["charge_state"]["preconditioning_enabled"],
            preconditioning_weekdays_only=False
            if vehicle["charge_state"]["preconditioning_times"] == "all_week"
            else True,
            off_peak_charging_enabled=vehicle["charge_state"][
                "off_peak_charging_enabled"
            ],
            off_peak_charging_weekdays_only=False
            if vehicle["charge_state"]["off_peak_charging_times"] == "all_week"
            else True,
            end_off_peak_time=vehicle["charge_state"]["off_peak_hours_end_time"],
        )
        if verbose:
            print("Scheduled departure set")
    except Exception as e:
        print(e)


def calc_schedule_limits(
    df,
    vehicle,
    events,
    verbose=False,
    time_zone=os.environ.get("TIME_ZONE", "America/New_York"),
):
    """
    This function calculates the charge limit, charge current request, scheduled departure time,
    and off peak charging end time for a Tesla vehicle based on the user's calendar events and
    the vehicle's current state.

    Parameters:
    - df (pandas.DataFrame): A DataFrame containing the user's calendar events.
    - vehicle (teslapy.Vehicle): A TeslaPy object with dictionary access and API request support
    - events (list): A list of calendar events.
    - time_zone (str): The time zone to use for the scheduled departure time and off peak charging end time.
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Returns:
    - vehicle (dict): A dictionary containing the updated vehicle information.
    """
    now = datetime.datetime.now(pytz.utc)
    mean_load = df["load_forecast"].mean()
    lower_std_load = mean_load - df["load_forecast"].std()
    upper_std_load = mean_load + df["load_forecast"].std()
    current_load = df.loc[(df["interval_start_utc"] - now).abs().idxmin()][
        "load_forecast"
    ]

    # Calculate charge limit soc
    longest_distance = -1
    valid_events = []
    for event in events:
        if "location" in event and "http" not in event["location"]:
            event["distance"], event["time"] = get_directions(
                os.environ.get("TESLA_ADDRESS"), event["location"]
            )
            # only consider events that are at least 15 minutes away
            if event["time"] > 15:
                valid_events.append(event)
                if event["distance"] > longest_distance:
                    longest_distance = event["distance"]
                    farthest_event = event

    # drives longer than 120 miles and longer than 3 hours
    if longest_distance >= 120:
        # higher than this will charge now at full rate
        vehicle["charge_state"]["charge_limit_soc"] = 94
    elif longest_distance > 0:
        vehicle["charge_state"]["charge_limit_soc"] = int(
            longest_distance * (95 - 75) / 120 + 75
        )
    else:
        vehicle["charge_state"]["charge_limit_soc"] = 75

    # Calculate scheduled departure time
    if len(valid_events) > 0:
        first_event_departure_time = datetime.datetime.fromisoformat(
            valid_events[0]["start"]["dateTime"]
        ) - datetime.timedelta(minutes=valid_events[0]["time"] + 30)
        farthest_event_departure_time = datetime.datetime.fromisoformat(
            farthest_event["start"]["dateTime"]
        ) - datetime.timedelta(minutes=farthest_event["time"] + 30)
        departure_time = (
            first_event_departure_time
            if first_event_departure_time <= farthest_event_departure_time
            else farthest_event_departure_time
        ).astimezone(pytz.timezone(time_zone))
        scheduled_departure_time_minutes = int(
            departure_time.hour * 60
            + departure_time.minute
            - departure_time.minute % 15
        )
        vehicle["charge_state"]["scheduled_departure_time_minutes"] = (
            scheduled_departure_time_minutes
            if scheduled_departure_time_minutes < 600
            else 600  # not past 10am
        )
    else:
        # 8am if no events
        vehicle["charge_state"]["scheduled_departure_time_minutes"] = 480

    # Calculate off peak charging end time
    filtered_df = (
        df[
            (df["load_forecast"] < lower_std_load)
            & (df["load_forecast"].shift(-1) >= lower_std_load)
        ]["interval_start_utc"]
        - now
    )
    off_peak_hours_end_time = df["interval_start_utc"][
        filtered_df[filtered_df >= datetime.timedelta(0)].idxmin()
    ].astimezone(pytz.timezone(time_zone))
    vehicle["charge_state"]["off_peak_hours_end_time"] = int(
        off_peak_hours_end_time.hour * 60
        + off_peak_hours_end_time.minute
        - off_peak_hours_end_time.minute % 15
    )

    # Calculate charge current request
    if vehicle["charge_state"]["charging_state"] == "Charging":
        if (
            now
            + datetime.timedelta(hours=vehicle["charge_state"]["time_to_full_charge"])
            > datetime.datetime.fromtimestamp(
                vehicle["charge_state"]["scheduled_departure_time"],
                pytz.timezone(time_zone),
            )
            and vehicle["charge_state"]["charge_current_request"]
            < vehicle["charge_state"]["charge_current_request_max"]
        ):
            vehicle["charge_state"]["charge_current_request"] = int(
                vehicle["charge_state"]["charge_current_request"] + 1
            )
            if verbose:
                print("Increased charge current request")
        elif (
            now
            + datetime.timedelta(hours=vehicle["charge_state"]["time_to_full_charge"])
            < off_peak_hours_end_time
            and vehicle["charge_state"]["charge_current_request"] > 5
        ):
            vehicle["charge_state"]["charge_current_request"] = int(
                vehicle["charge_state"]["charge_current_request"] - 1
            )
            if verbose:
                print("Decreased charge current request")
        else:
            if verbose:
                print("No change to charge current request")
    # If on a lower power plug (NEMA 5-15), set the charge current to 12A
    elif (
        vehicle["charge_state"]["charge_current_request_max"] > 12
        and current_load <= mean_load
    ):
        vehicle["charge_state"]["charge_current_request"] = int(
            0.5 * vehicle["charge_state"]["charge_current_request_max"]
        )
    else:
        vehicle["charge_state"]["charge_current_request"] = vehicle["charge_state"][
            "charge_current_request_max"
        ]

    return vehicle


def optimize_vehicle_charge(df, tesla, vehicle, events, verbose=False):
    """
    This function optimizes the charging schedule for a Tesla vehicle based on
    grid status and calendar events and then calls the Tesla API to set the
    charging schedule.

    Parameters:
    - df (pandas.DataFrame): A DataFrame containing the grid status.
    - tesla (teslapy.Tesla): A TeslaPy object authenticated with the user's Tesla account.
    - vehicle (teslapy.Vehicle): A TeslaPy object with dictionary access and API request support
    - events (list): A list of calendar events.
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Returns:
    - None
    """
    modified = False
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), end=" ")

    if (
        vehicle["charge_state"]["charge_port_door_open"] is not True
        or vehicle["charge_state"]["conn_charge_cable"] == "<invalid>"
    ):
        print(f"{vehicle['display_name']} is not plugged in. No action taken.")
    elif vehicle["charge_state"]["fast_charger_present"] is True:
        print(f"{vehicle['display_name']} is supercharging. No action taken.")
    elif vehicle["charge_state"]["charging_state"] == "NoPower":
        print(
            f"{vehicle['display_name']} is plugged in, but there is no power. No action taken."
        )
    elif vehicle["charge_state"]["charge_limit_soc"] >= 95:
        if vehicle["charge_state"]["charging_state"] != "Charging":
            print(
                f"{vehicle['display_name']} is plugged in and set to above 95%. Starting charge at full power."
            )
            vehicle["charge_state"]["charge_current_request"] = vehicle["charge_state"][
                "charge_current_request_max"
            ]
            wake_up(vehicle, verbose)
            set_charge_current(df, vehicle, verbose)
            set_start_charging(vehicle, verbose)
            modified = True
        elif (
            vehicle["charge_state"]["charge_current_request"]
            != vehicle["charge_state"]["charge_current_request_max"]
        ):
            print(
                f"{vehicle['display_name']} is charging and set to above 95%. Increasing charge current."
            )
            vehicle["charge_state"]["charge_current_request"] = vehicle["charge_state"][
                "charge_current_request_max"
            ]
            wake_up(vehicle, verbose)
            set_charge_current(df, vehicle, verbose)
            modified = True
        else:
            print(
                f"{vehicle['display_name']} is charging and set to above 95%. No action taken."
            )
    elif vehicle["charge_state"]["battery_level"] < 20:
        if vehicle["charge_state"]["charging_state"] != "Charging":
            print(
                f"{vehicle['display_name']} is plugged in and below 20%. Starting charge at full power."
            )
            vehicle["charge_state"]["charge_current_request"] = vehicle["charge_state"][
                "charge_current_request_max"
            ]
            wake_up(vehicle)
            set_charge_current(df, vehicle, verbose)
            set_start_charging(vehicle, verbose)
            modified = True
        elif (
            vehicle["charge_state"]["charge_current_request"]
            != vehicle["charge_state"]["charge_current_request_max"]
        ):
            print(
                f"{vehicle['display_name']} is plugged in and below 20%. Increasing charge current."
            )
            vehicle["charge_state"]["charge_current_request"] = vehicle["charge_state"][
                "charge_current_request_max"
            ]
            wake_up(vehicle, verbose)
            set_charge_current(df, vehicle, verbose)
            modified = True
        else:
            print(
                f"{vehicle['display_name']} is charging and below 20%. No action taken."
            )
    elif (
        vehicle["charge_state"]["battery_level"] >= 20
        and vehicle["charge_state"]["charging_state"] == "Charging"
        and vehicle["charge_state"]["scheduled_charging_start_time"]
        and datetime.datetime.fromtimestamp(
            vehicle["charge_state"]["scheduled_charging_start_time"]
        )
        > datetime.datetime.now()
    ):
        print(
            f"{vehicle['display_name']} is charging and above 20% SoC. Stopping charge."
        )
        wake_up(vehicle, verbose)
        set_stop_charging(vehicle, verbose)
        modified = True
    elif (
        vehicle["charge_state"]["scheduled_charging_mode"] == "DepartBy"
        and vehicle["charge_state"]["off_peak_charging_enabled"] is True
    ):
        print(f"{vehicle['display_name']} is plugged in. Optimizing charge schedule.")
        vehicle = calc_schedule_limits(df, vehicle, events, verbose)
        wake_up(vehicle, verbose)
        set_charge_current(df, vehicle, verbose)
        set_charge_limit(vehicle, verbose)
        set_schedule(vehicle, verbose)
        modified = True
    else:
        print(
            f"{vehicle['display_name']} is plugged in but scheduled charging is not enabled. Enable it in the app to optimize charging at this location."
        )

    if modified:
        if verbose:
            print("Vehicle status modified. Waiting 5 seconds for updates.")
        time.sleep(5)
        get_vehicle_status(tesla, verbose=verbose)


def schedule_it(function, minutes):
    """
    Schedules a function to run every specified number of minutes.

    Parameters:
    - function (function): The function to be scheduled.
    - minutes (int): The number of minutes between each run of the function.

    Returns:
    - None
    """
    schedule.every(minutes).minutes.do(function)
    while True:
        schedule.run_pending()
        time.sleep(1)


def main(verbose=False):
    """
    The main function of the intelligent charge scheduler. This function authenticates with Tesla and Google APIs,
    retrieves the grid status, vehicle status, and calendar events, and optimizes the vehicle's charging schedule
    based on the grid status and calendar events. The function returns the grid status dataframe, the vehicle object,
    the calendar events list, and a summary of the vehicle's status.

    Parameters:
    - verbose (bool): A boolean indicating whether to print verbose output. Defaults to False.

    Returns:
    - None
    """
    google_cloud_storage_download(verbose=verbose)
    tesla = auth_tesla()
    google = auth_google()
    df = get_grid_status()

    vehicle, _ = get_vehicle_status(tesla, verbose=verbose)
    events = get_calendar_events(google, verbose=verbose)
    optimize_vehicle_charge(df, tesla, vehicle, events, verbose=verbose)
    tesla.close()
    google_cloud_storage_upload(verbose=verbose)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A grid aware, calendar connected charge scheduler for Tesla vehicles."
    )
    parser.add_argument(
        "-v", "--verbose", help="increase output verbosity", action="store_true"
    )
    parser.add_argument(
        "-i",
        "--interval",
        help="interval in minutes to run the main function",
        type=int,
    )
    args = parser.parse_args()

    # this is to run the function once at the start
    main(args.verbose)
    # this is to run the function every x minutes
    if args.interval:
        schedule_it(main, args.interval)
