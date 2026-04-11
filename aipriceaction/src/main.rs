mod cli;
mod constants;
mod test_proxy;
mod test_redis;
mod generate_company_info;
mod csv;
mod db;
mod models;
mod providers;
mod queries;
mod redis;
mod server;
mod services;
mod workers;

fn main() {
    cli::run();
}
