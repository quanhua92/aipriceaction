mod cli;
mod constants;
mod test_proxy;
mod generate_company_info;
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
