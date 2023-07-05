"""
This module is responsible for scheduling the charging of a Tesla vehicle based on grid
demand and a Google calendar.It fetches the grid status, vehicle status, and optimizes
the vehicle charge accordingly.

Author: Sam Sipe
"""
__version__ = "0.2.0"

from dotenv import load_dotenv
import argparse
import schedule
import pytz
import time
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
        os.getenv("TESLA_ACCOUNT_EMAIL"),
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
        cache_time = datetime.datetime.fromtimestamp(os.path.getmtime(cache_file))
        if datetime.datetime.now() - cache_time < datetime.timedelta(
            hours=cache_max_age_hours
        ):
            # Load cached data and return
            df = pd.read_parquet(cache_file)
            return df

    # Fetch new data and cache it
    client = GridStatusClient(api_key)
    df = client.get_dataset(
        dataset=iso.lower() + "_load_forecast",
        start=(datetime.datetime.now() - datetime.timedelta(hours=6)).strftime(
            "%Y-%m-%dT%H:%M"
        ),
        end=(datetime.datetime.now() + datetime.timedelta(hours=66)).strftime(
            "%Y-%m-%dT%H:%M"
        ),
    )
    df.to_parquet(cache_file, index=False)
    return df


def plot_grid_status(df):
    """Creates a plot of the percentage of the grid that is on fire over time.

    Args:
        df: A dataframe containing a column named "grid_status" that contains
            the status of the grid (e.g., "on_fire", "burned_out", etc.).

    Returns:
        None.
    """
    mean_load = df["load_forecast"].mean()
    lower_std_load = mean_load - df["load_forecast"].std()
    upper_std_load = mean_load + df["load_forecast"].std()
    df["interval_start"] = pd.to_datetime(df["interval_start_utc"]).dt.tz_convert(
        "US/Eastern"
    )
    now = datetime.datetime.now()
    fig = px.line(
        df,
        x="interval_start",
        y="load_forecast",
        title="Load forecast for " + os.environ.get("GRID_ISO"),
    )
    fig.add_trace(
        go.Scatter(
            x=df["interval_start"],
            y=[upper_std_load] * len(df),
            mode="lines",
            name="Upper Std Load",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["interval_start"],
            y=[mean_load] * len(df),
            mode="lines",
            name="Mean Load",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["interval_start"],
            y=[lower_std_load] * len(df),
            mode="lines",
            name="Lower Std Load",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[now, now],
            y=[df["load_forecast"].min(), df["load_forecast"].max()],
            mode="lines",
            name="Time Now",
            line=dict(color="red", dash="dash"),
        )
    )
    return fig


def calc_charge_limit_factor(df):
    """
    This function calculates the charge limit state of charge (SoC) based on the
    current grid status. It uses the mean, lower standard deviation, and upper standard
    deviation of the load forecast data in a pandas DataFrame to determine the charge
    limit SoC.

    Parameters:
    - df (pandas.DataFrame): DataFrame containing the load forecast data.

    Returns:
    - float: The charge limit SoC as a factor between .25 and 1.
    """
    mean_load = df["load_forecast"].mean()
    lower_std_load = mean_load - df["load_forecast"].std()
    upper_std_load = mean_load + df["load_forecast"].std()
    current_load = df.loc[
        (df["interval_start_utc"] - datetime.datetime.now(pytz.utc)).abs().idxmin()
    ]["load_forecast"]

    if current_load >= upper_std_load:
        return 0.25
    elif current_load <= lower_std_load:
        return 1
    else:
        return round(
            (current_load - lower_std_load)
            * (0.25 - 0.75)
            / (upper_std_load - lower_std_load)
            + 0.75,
            2,
        )


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
        - 'start_time': The start time of the event as a datetime object.
        - 'end_time': The end time of the event as a datetime object.
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
                print("Events in the next 24 hours with a location:")
                for event in events:
                    if "location" in event:
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
    directions_result = gmaps.directions(origin, destination, mode="driving")

    return (
        round(directions_result[0]["legs"][0]["distance"]["value"] / 1609.34, 1),
        round(directions_result[0]["legs"][0]["duration"]["value"] / 60, 1),
    )


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
    vehicles = tesla.vehicle_list()
    try:
        last_seen = vehicles[0].last_seen()
    except ValueError:
        last_seen = "just now"

    summary = f"{vehicles[0]['display_name']} is {vehicles[0]['state']} and was last seen {last_seen} at {vehicles[0]['charge_state']['battery_level']}% SoC"
    if verbose:
        print(summary)
    return vehicles[0], summary


def wake_up(vehicle, verbose=False):
    """
    Function to wake up a Tesla vehicle.

    Args:
    - vehicle (dict): A dictionary containing the vehicle information.
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
    except teslapy.VehicleError as e:
        print(e)


def set_charge_current(df, vehicle, verbose=False):
    """
    This function sets the charging current of a Tesla vehicle.

    Parameters:
    - vehicle (dict): A dictionary containing the vehicle information.
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Raises:
    - teslapy.VehicleError: If there is an error with the vehicle.

    Returns:
    - None
    """
    # If the vehicle is plugged in and the charge limit is above 95%, charge at full rate
    if vehicle["charge_state"]["charge_limit_soc"] >= 95:
        vehicle["charge_state"]["charge_current_request"] = vehicle["charge_state"][
            "charge_current_request_max"
        ]
    # If on a lower power plug (NEMA 5-15), set the charge current to 9A
    elif vehicle["charge_state"]["charge_current_request_max"] > 12:
        vehicle["charge_state"]["charge_current_request"] = int(
            calc_charge_limit_factor(df)
            * vehicle["charge_state"]["charge_current_request_max"]
        )
    else:
        vehicle["charge_state"]["charge_current_request"] = vehicle["charge_state"][
            "charge_current_request_max"
        ]

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
    - vehicle (dict): A dictionary containing the vehicle information.
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
    - vehicle (dict): A dictionary containing the vehicle information.
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
    - vehicle (dict): A dictionary containing the vehicle information.
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
    - vehicle (dict): A dictionary containing the vehicle information.
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
    except teslapy.VehicleError as e:
        print(e)


def optimize_vehicle_charge(df, vehicle, events, verbose=False):
    """
    This function optimizes the charging schedule for a Tesla vehicle based on
    grid status and calendar events and then calls the Tesla API to set the
    charging schedule.

    Parameters:
    - df (pandas.DataFrame): A DataFrame containing the grid status.
    - vehicle (dict): A dictionary containing the vehicle information.
    - events (list): A list of calendar events.
    - verbose (bool): Whether to print verbose output. Defaults to False.

    Returns:
    - None
    """
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
            wake_up(vehicle, verbose)
            set_charge_current(df, vehicle, verbose)
            set_start_charging(vehicle, verbose)
        elif (
            vehicle["charge_state"]["charge_current_request"]
            != vehicle["charge_state"]["charge_current_request_max"]
        ):
            print(
                f"{vehicle['display_name']} is charging and set to above 95%. Increasing charge current."
            )
            wake_up(vehicle, verbose)
            set_charge_current(df, vehicle, verbose)
        else:
            print(
                f"{vehicle['display_name']} is charging and set to above 95%. No action taken."
            )
    elif vehicle["charge_state"]["battery_level"] < 20:
        if vehicle["charge_state"]["charging_state"] != "Charging":
            print(
                f"{vehicle['display_name']} is plugged in and below 20% SoC. Starting charge."
            )
            wake_up(vehicle)
            set_charge_current(df, vehicle, verbose)
            set_start_charging(vehicle, verbose)
        else:
            print(
                f"{vehicle['display_name']} is charging and below 20% SoC. No action taken."
            )
    elif (
        vehicle["charge_state"]["battery_level"] >= 20
        and vehicle["charge_state"]["charging_state"] == "Charging"
        and vehicle["charge_state"]["scheduled_charging_start_time"]
        and datetime.datetime.fromtimestamp(
            vehicle["charge_state"]["scheduled_charging_start_time"]
        )
        >= datetime.datetime.now()
    ):
        wake_up(vehicle, verbose)
        set_stop_charging(vehicle, verbose)
    elif (
        vehicle["charge_state"]["scheduled_charging_mode"] == "DepartBy"
        and vehicle["charge_state"]["off_peak_charging_enabled"] is True
    ):
        print(f"{vehicle['display_name']} is plugged in. Optimizing charge schedule.")
        # TODO only run the next few lines if the vehicle hasn't been woken up for a
        # while (every hour or so)
        wake_up(vehicle, verbose)
        set_charge_current(df, vehicle, verbose)
        # TODO set the limit from the google calendar
        set_charge_limit(vehicle, verbose)
        # TODO set the departure times and off peak times from grid status
        set_schedule(vehicle, verbose)
    else:
        print(
            f"{vehicle['display_name']} is plugged in but scheduled charging is not enabled. Enable it in the app to optimize charging at this location."
        )


def schedule_it(function, minutes):
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

    :param verbose: A boolean indicating whether to print verbose output.
    :return: A tuple containing the grid status dataframe, the vehicle object, the calendar events list, and a summary
             of the vehicle's status.
    """
    tesla = auth_tesla()
    google = auth_google()
    df = get_grid_status()

    vehicle, summary = get_vehicle_status(tesla, verbose=verbose)
    events = get_calendar_events(google, verbose=verbose)
    optimize_vehicle_charge(df, vehicle, events, verbose=verbose)
    tesla.close()
    return df, vehicle, events, summary


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
