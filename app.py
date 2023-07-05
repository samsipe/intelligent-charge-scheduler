import datetime
from dash import Dash, html, dcc, Input, Output
import dash_bootstrap_components as dbc

import scheduler

app = Dash(
    __name__,
    title="Intelligent Charging Scheduler",
    external_stylesheets=[dbc.themes.ZEPHYR],
    meta_tags=[
        {"name": "viewport", "content": "width=device-width, initial-scale=1"},
    ],
    suppress_callback_exceptions=True,
)
server = app.server


@app.callback(
    Output("summary", "children"), Input("interval-component", "n_intervals")
)
def scheduler_data(n):
    tesla = scheduler.auth_tesla()

    _, summary = scheduler.get_vehicle_status(tesla)
    tesla.close()
    return f"{summary}"


@app.callback(
    Output("forecast_load", "figure"), Input("interval-component", "n_intervals")
)
def forecast_load(n):
    df = scheduler.get_grid_status()
    fig = scheduler.plot_grid_status(df)
    return fig


location = dcc.Location(id="url", refresh=False)

footer = dbc.Navbar(
    dbc.Container(
        [
            html.Div(
                f"© {datetime.datetime.now().year} Intelligent Charging Scheduler"
            ),
            html.Div(
                [
                    "Made with ⚡️ by ",
                    html.A(
                        "Sam Sipe",
                        href="https://samsipe.com/",
                        target="blank",
                        style={"textDecoration": "none"},
                    ),
                ]
            ),
        ],
    ),
    color="light",
    className="fixed-bottom",
)

counter = dcc.Interval(
    id="interval-component",
    interval=900000,  # update every 15 minutes
    n_intervals=0,
)

app.layout = html.Div(
    [
        location,
        html.H1(
            children="Intelligent Charging Scheduler", style={"textAlign": "center"}
        ),
        dcc.Graph(id="forecast_load", style={"height": "80vh"}),
        html.H4(id="summary", style={"textAlign": "center"}),
        footer,
        counter,
    ]
)

if __name__ == "__main__":
    app.run_server(debug=True)
