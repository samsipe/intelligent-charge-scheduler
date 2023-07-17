import datetime
import json
from dash import Dash, html, dcc, Input, Output, State
import dash_bootstrap_components as dbc

from scheduler import get_grid_status, plot_grid_status, google_cloud_storage_download

app = Dash(
    __name__,
    title="Intelligent Charge Scheduler",
    external_stylesheets=[dbc.themes.ZEPHYR],
    meta_tags=[
        {"name": "viewport", "content": "width=device-width, initial-scale=1"},
    ],
    suppress_callback_exceptions=True,
)
server = app.server

nav = dbc.Nav(
    [
        dbc.NavItem(
            dbc.NavLink(
                "GitHub",
                href="https://github.com/samsipe/intelligent-charge-scheduler",
                target="blank",
                id="git_hub",
                style={"textAlign": "center"},
            )
        ),
    ],
    pills=True,
    className="g-0 ms-auto flex-nowrap mt-3 mt-md-0",
)

navbar = dbc.Navbar(
    dbc.Container(
        [
            html.A(
                # Use row and col to control vertical alignment of logo / brand
                dbc.Row(
                    [
                        dbc.Col(
                            html.Img(src="assets/apple-touch-icon.png", height="35px")
                        ),
                        dbc.Col(
                            dbc.NavbarBrand(
                                "Intelligent Charge Scheduler",
                                className="ms-2",
                                style={"font-size": "120%"},
                            )
                        ),
                    ],
                    align="center",
                    className="g-0",
                ),
                href="/",
                style={"textDecoration": "none"},
            ),
            dbc.NavbarToggler(id="navbar-toggler", n_clicks=0),
            dbc.Collapse(
                nav,
                id="navbar-collapse",
                is_open=False,
                navbar=True,
            ),
        ]
    ),
    color="dark",
    dark=True,
)


# add callback for toggling the collapse on small screens
@app.callback(
    Output("navbar-collapse", "is_open"),
    [Input("navbar-toggler", "n_clicks")],
    [State("navbar-collapse", "is_open")],
)
def toggle_navbar_collapse(n, is_open):
    if n:
        return not is_open
    return is_open


@app.callback(
    Output("forecast_load", "figure"),
    Output("summary", "children"),
    Output("charge_limit", "children"),
    Output("charge_current", "children"),
    Output("charging_state", "children"),
    Input("interval-component", "n_intervals"),
)
def forecast_load(n):
    google_cloud_storage_download()
    df = get_grid_status()
    with open("vehicle.json", "r") as vehicle_file:
        vehicle = json.load(vehicle_file)
    fig = plot_grid_status(df, vehicle=vehicle)
    return (
        fig,
        vehicle["summary"],
        f"Charge Limit: {vehicle['charge_state']['charge_limit_soc']}%",
        f"Charge Current: {vehicle['charge_state']['charge_current_request']}A/{vehicle['charge_state']['charge_current_request_max']}A",
        f"Charge State: {vehicle['charge_state']['charging_state']}",
    )


dashboard = dbc.Container(
    [
        html.Div(
            id="graph_wrapper",
            children=[
                html.H5(
                    "Optimal Tesla Charging Using Load Forecast",
                    className="mt-5",
                    style={"textAlign": "center"},
                ),
                dcc.Graph(
                    id="forecast_load",
                    style={"height": "40vh"},
                ),
                html.Div(
                    [
                        dbc.Row(
                            dbc.Col(
                                html.H6(id="summary", style={"textAlign": "center"})
                            )
                        ),
                        dbc.Row(
                            [
                                dbc.Col(
                                    html.H6(
                                        id="charge_limit", style={"textAlign": "center"}
                                    )
                                ),
                                dbc.Col(
                                    html.H6(
                                        id="charge_current",
                                        style={"textAlign": "center"},
                                    )
                                ),
                                dbc.Col(
                                    html.H6(
                                        id="charging_state",
                                        style={"textAlign": "center"},
                                    )
                                ),
                            ]
                        ),
                    ]
                ),
            ],
        ),
    ]
)

location = dcc.Location(id="url", refresh=False)

footer = dbc.Navbar(
    dbc.Container(
        [
            html.Div(f"© {datetime.datetime.now().year} Intelligent Charge Scheduler"),
            html.Div(
                [
                    "Made with 🔋 by ",
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
    class_name="fixed-bottom",
)

counter = dcc.Interval(
    id="interval-component",
    interval=15000,  # update every 15 seconds
    n_intervals=0,
)

app.layout = html.Div(
    [
        location,
        navbar,
        html.Div(dashboard, className="pb-3 mb-5"),
        footer,
        counter,
    ]
)

if __name__ == "__main__":
    app.run_server(debug=True)
