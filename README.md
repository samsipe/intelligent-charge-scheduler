# Intelligent-Charge-Scheduler

A grid aware, calendar connected charge scheduler for Tesla vehicles

- [Intelligent-Charge-Scheduler](#intelligent-charge-scheduler)
    - [Overview](#overview)
    - [Limitations:](#limitations)
    - [Environment and Setup:](#environment-and-setup)
    - [Basic Usage](#basic-usage)
    - [Docker Usage](#docker-usage)
    - [Basic Logic:](#basic-logic)
    - [Web Frontend](#web-frontend)

### Overview

This is an MVP for a charge optimizer that will check a google calendar, the status of the electric grid, and the state of charge of the vehicle to determine when to best charge a Tesla. It is designed to run on a schedule, and can run locally, in docker compose, or deployed to the cloud. It will also only wake the vehicle when it is plugged in, so it won't drain the battery.

### Limitations:

- Single location (where the car is usually charged)
- Single vehicle (lucky you if you have more than one)
- Single calendar (Google for now)

### Environment and Setup:

To get this to work in your own environment, please follow a few setup steps:

- Get a [Grid Status API Key](https://www.gridstatus.io/api)
- Select your [ISO](https://www.gridstatus.io/map)
- A `configuration.json` file, saved from the [Google Calendar API](https://developers.google.com/calendar/api/quickstart/python) setup
- Get a [Google Maps API Key](https://console.cloud.google.com/google/maps-apis/credentials?authuser=1&project=intelligent-charge-scheduler)
- Use the above to make a new `.env` file with the following:

```
TESLA_ACCOUNT_EMAIL=you@email.com
GRID_STATUS_API_KEY=<key_goes_here>
GRID_ISO=PJM
GOOGLE_MAPS_API_KEY=<key_goes_here>
TESLA_HOME_ADDRESS="28047 County Street, Nowhere, OK 73038"
```

### Basic Usage

- Setup python environment and install requirements

```
python3 -m venv .venv && \
source .venv/bin/activate && \
pip3 install --upgrade pip && \
pip3 install -r requirements.txt
```

- You can run this straight from the python module, and it will run until closed:

```
python3 scheduler.py -v -i 14
```

- Follow the directions to authorize the Tesla app.

### Docker Usage

If you run the above script before using docker, you will already have the required `token.json` and `cache.json` files and will not need to re-authorize your Tesla or google calendar.

> ⚠️ This means you shouldn't push docker images you build to a public repo, as they will contain short-lived secrets. To change this uncomment the last two lines of the [.dockerignore](.dockerignore) file.

- Running this in docker using docker compose is better for a longer running process:

```
docker-compose up --build
```

- To authorize the Tesla app, follow the link and copy the url after the "Page Not Found" shows up, then in a new terminal window run:

```
docker attach intelligent-charge-scheduler-scheduler-1
```

- Now paste the link, hit enter, and close the window.

- When you're done you can use the following to remove everything:

```
docker-compose down --rmi all -v --remove-orphans
```

### Basic Logic:

- Run the python script on a schedule
- Set Max charging amps hourly based on Grid Status
- If the the charge state is below 20% and plugged in
- Stop charging once the charge state is above 20% and wait for scheduler to start
- If the car's charge limit is set above 95% start charging at full power
- Set `SCHEDULED_DEPARTURE` based on the following (requires wake)
  - Only run if `off_peak_charging_enabled == True` (prevents adding new locations)
  - Car must be set to Off-Peak/ DepartBy Charging
  - Car must not be less than 20%
- Set `CHANGE_CHARGE_LIMIT` based on how far away the next calendar event is (requires wake)
  - Only increase limit above 80% if within a few hours of departure (work in progress)
- Set `CHARGING_AMPS` based on forecast grid load over the next week (requires wake)
  - 25% above upper standard deviation
  - 50% above mean
  - 75% below mean
  - 100% below lower standard deviation

### Web Frontend

There is also a [web frontend](app.py) that can be used to view the current state of the scheduler, built using [Plotly Dash](https://dash.plotly.com) It can also be run locally, in docker compose, or deployed to the cloud. It shares authentication and data with the scheduler, so it will only work if the scheduler has run recently.

```
python3 app.py
```
