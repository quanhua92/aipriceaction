mod cli;
mod constants;
mod csv;
mod db;
mod models;
mod providers;
mod queries;
mod server;
mod services;
mod workers;

fn main() {
    cli::run();
}
