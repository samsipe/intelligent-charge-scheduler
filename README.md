# Intelligent-Charge-Scheduler

A grid aware, calendar connected charge scheduler for Tesla vehicles

- [Intelligent-Charge-Scheduler](#intelligent-charge-scheduler)
  - [Overview](#overview)
    - [Limitations](#limitations)
  - [Environment and Setup](#environment-and-setup)
  - [Basic Usage](#basic-usage)
  - [Docker Usage](#docker-usage)
  - [Basic Logic](#basic-logic)
  - [Web Frontend](#web-frontend)
  - [GitHub CopilotX](#github-copilotx)
  - [Disclaimer](#disclaimer)

## Overview

This is an MVP for a charge optimizer that checks a google calendar for events, looks at the status of load on the electric grid, and the state of charge of a vehicle to determine when to best charge its batteries. It runs on a schedule, and can run locally, in docker using docker compose, or deployed to the cloud. It also only wakes the vehicle when it is plugged in, so it won't drain the battery.

The Tesla app already allows users with Time of Use (TOU) rates to set **off-peak charge** end time. This allows the vehicle to charge at the lowest cost possible, and be ready to go when needed. This app takes that a step further by using a calendar to determine when the vehicle will be needed, and the status of the electric grid to determine when and **_how fast_** it should charge. It will also increase the charge limit if the vehicle is needed for a longer trip that is on your calendar.

To use this application you will need to enable **off-peak charge** in the Tesla mobile app. This app will then change the **charge limit, charge rate, departure time, and off-peak end time** accordingly. It will not change other schedule settings like preconditioning, or weekday/weekend settings.

### Limitations

- Single location (where the car is usually charged)
- Single vehicle (lucky you if you have more than one)
- Single calendar (Google for now)

## Environment and Setup

To get this to work in your own environment, please follow a few setup steps:

- Get a [Grid Status API Key](https://www.gridstatus.io/api)
- Select your [ISO](https://www.gridstatus.io/map)
- A `configuration.json` file, saved from the [Google Calendar API](https://developers.google.com/calendar/api/quickstart/python) setup
- Get a [Google Maps API Key](https://console.cloud.google.com/google/maps-apis/credentials?authuser=1&project=intelligent-charge-scheduler)
- Use the above to make a new `.env` file with the following:

```shell
TESLA_ACCOUNT_EMAIL=your@email.com
GRID_STATUS_API_KEY=<key_goes_here>
GRID_ISO=PJM
GOOGLE_MAPS_API_KEY=<key_goes_here>
TESLA_HOME_ADDRESS="28047 County Street Nowhere OK 73038"
TIME_ZONE=America/New_York
```

## Basic Usage

- Setup python environment and install requirements

```shell
python3 -m venv .venv && \
source .venv/bin/activate && \
pip3 install --upgrade pip && \
pip3 install -r requirements.txt
```

- You can run this straight from the python module, and it will run until closed:

```shell
python3 scheduler.py -v -i 14
```

- Follow the directions to authorize the Tesla app.

## Docker Usage

If you run the above script before using docker, you will already have the required `token.json` and `cache.json` files and will not need to re-authorize your Tesla or google calendar, if you comment out the following lines in the [.dockerignore](.dockerignore)

```requirements.txt
# Intelligent Charge Scheduler
*.json
*.ipynb
*.parquet
```

> ⚠️ This means you shouldn't push docker images you build this way to a public repo, as they will contain short-lived secrets.

- Running this in docker using docker compose is better for a longer running process:

```shell
docker compose up --build
```

- To authorize the Tesla app, follow the link and copy the url after the "Page Not Found" shows up, then in a new terminal window run:

```shell
docker attach intelligent-charge-scheduler-scheduler-1
```

- Now paste the link, hit enter, and close the window.

- When you're done you can use the following to remove everything:

```shell
docker compose down --rmi all -v --remove-orphans
```

## Basic Logic

- Run the python script on a schedule (every 15 minutes)
- If the charge state is below 20% and the vehicle is plugged in
  - Charge at full power
  - Stop charging once the charge state is above 20% and wait for scheduler to start
- If the car's charge limit is set above 95% start charging at full power
- Set `SCHEDULED_DEPARTURE` based on the following (requires wake)
  - Only run if `off_peak_charging_enabled == True` (prevents adding new locations)
  - Car must be set to Off-Peak/ DepartBy Charging
  - Car must not be less than 20%
- Set `CHANGE_CHARGE_LIMIT` based on how far away the next calendar event is (requires wake)
  - Upcoming trips longer than 3 hours and 120 miles will get a charge limit of 94%
  - Only increase limit above 80% if within a few hours of departure (work in progress)
- Set `CHARGING_AMPS` based on forecast grid load over the next week (requires wake)

## Web Frontend

There is also a [web frontend](app.py) that can be used to view the current state of the scheduler, built using [Plotly Dash](https://dash.plotly.com). It can also be run locally, in docker compose, or deployed to the cloud. It shares authentication and data with the scheduler, so it will only work if the scheduler has run recently.

```shell
python3 app.py
```

## GitHub CopilotX

I initially started this project as a way to test out the features of [GitHub's CopilotX](https://github.com/features/preview/copilot-x). It was able to generate a lot of the boilerplate code, write docstrings, and help with errors but it was not able to generate the logic for the scheduler. It also offers up a lot of answers that are wrong, or just confusing. I'm sure it will get better over time, but it's not quite there yet. (It wrote that last sentence, not me)

## Disclaimer

The creator of this application is not responsible for any issues that may arise from use of this app with Tesla vehicles. Use of this app with a Tesla vehicle is at the sole risk of the vehicle owner/operator. By using this app you accept all risks associated with modifying the behavior of a Tesla vehicle via a third-party application. The app creator provides no warranty that the app will function properly, nor do they accept any liability for any outcomes arising from use of the app with a Tesla vehicle. You assume full responsibility for the results of using this application with your Tesla vehicle.
